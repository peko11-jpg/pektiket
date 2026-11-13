import logging
import uuid

import stripe
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.event import TicketType
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus, StripeEvent
from app.models.ticket import Ticket
from app.services.orders import _fail, _return_stock, release_expired

stripe.api_key = settings.stripe_secret_key
logger = logging.getLogger(__name__)

CURRENCY = "eur"
MIN_AMOUNT_CENTS = 50  # Stripe ne prima manje od 0,50 €


def create_payment(
    db: Session, user_id: uuid.UUID, order_id: uuid.UUID
) -> tuple[Payment, str]:
    release_expired(db)

    order = db.scalar(
        select(Order)
        .where(Order.id == order_id, Order.user_id == user_id)
        .with_for_update()
    )
    if order is None:
        _fail(db, 404, "ORDER_NOT_FOUND", "Porudžbina ne postoji")
    if order.status != OrderStatus.pending:
        _fail(db, 409, "ORDER_NOT_PENDING", "Porudžbina više nije na čekanju")
    if order.total_cents < MIN_AMOUNT_CENTS:
        _fail(db, 400, "AMOUNT_TOO_SMALL", "Iznos je ispod minimuma za online plaćanje")

    try:
        # isti idempotency_key: dvostruki klik ne pravi dva računa
        intent = stripe.PaymentIntent.create(
            amount=order.total_cents,
            currency=CURRENCY,
            payment_method_types=["card"],
            metadata={"order_id": str(order.id)},
            idempotency_key=f"order-{order.id}",
        )
    except stripe.StripeError:
        logger.exception("Stripe PaymentIntent create failed")
        _fail(db, 502, "PAYMENT_PROVIDER_ERROR", "Plaćanje trenutno nije dostupno")

    payment = db.scalar(select(Payment).where(Payment.order_id == order.id))
    if payment is None:
        payment = Payment(
            order_id=order.id,
            stripe_payment_intent_id=intent.id,
            amount_cents=order.total_cents,
            currency=CURRENCY,
        )
        db.add(payment)
    db.commit()
    return payment, intent.client_secret


def _take_stock(db: Session, order: Order) -> bool:
    """Ponovo zauzmi karte (ako su u međuvremenu oslobođene). Atomski."""
    result = db.execute(
        update(TicketType)
        .where(
            TicketType.id == order.ticket_type_id,
            TicketType.sold_quantity + order.quantity <= TicketType.total_quantity,
        )
        .values(sold_quantity=TicketType.sold_quantity + order.quantity)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _refund(intent_id: str) -> None:
    stripe.Refund.create(
        payment_intent=intent_id, idempotency_key=f"refund-{intent_id}"
    )


def _on_payment_succeeded(db: Session, intent: dict) -> None:
    payment = db.scalar(
        select(Payment)
        .where(Payment.stripe_payment_intent_id == intent["id"])
        .with_for_update()
    )
    if payment is None:
        logger.warning("Nepoznat PaymentIntent %s", intent["id"])
        return
    if payment.status in (PaymentStatus.succeeded, PaymentStatus.refunded):
        return  # već obrađeno

    order = db.scalar(
        select(Order).where(Order.id == payment.order_id).with_for_update()
    )
    amount_ok = (
        intent.get("amount") == order.total_cents
        and intent.get("currency") == payment.currency
    )

    if order.status == OrderStatus.pending:
        stock_ok = True  # karte su još zadržane za ovu porudžbinu
    else:
        # rezervacija je istekla ili otkazana: probaj ponovo zauzeti karte
        stock_ok = amount_ok and _take_stock(db, order)

    if not (amount_ok and stock_ok):
        if order.status == OrderStatus.pending:
            order.status = OrderStatus.cancelled
            _return_stock(db, order.ticket_type_id, order.quantity)
        payment.status = PaymentStatus.refunded
        _refund(intent["id"])
        return

    order.status = OrderStatus.paid
    payment.status = PaymentStatus.succeeded
    for _ in range(order.quantity):
        db.add(
            Ticket(
                order_id=order.id,
                ticket_type_id=order.ticket_type_id,
                owner_id=order.user_id,
            )
        )


def _on_payment_failed(db: Session, intent: dict) -> None:
    payment = db.scalar(
        select(Payment)
        .where(Payment.stripe_payment_intent_id == intent["id"])
        .with_for_update()
    )
    if payment is not None and payment.status == PaymentStatus.created:
        payment.status = PaymentStatus.failed


def process_stripe_event(db: Session, event: dict) -> None:
    # 1) zapiši ID poruke; ako već postoji, ovu poruku smo već obradili
    db.add(StripeEvent(id=event["id"]))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return

    # 2) obradi poruku
    obj = event["data"]["object"]
    if event["type"] == "payment_intent.succeeded":
        _on_payment_succeeded(db, obj)
    elif event["type"] == "payment_intent.payment_failed":
        _on_payment_failed(db, obj)

    # 3) sve ili ništa: ID poruke i posljedice se čuvaju zajedno
    db.commit()