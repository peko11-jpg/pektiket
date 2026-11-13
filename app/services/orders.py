import uuid
from datetime import timedelta
from typing import NoReturn

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload

from app.core.errors import AppError
from app.models.event import TicketType, utcnow
from app.models.order import Order, OrderStatus
from app.models.user import User

RESERVATION_MINUTES = 10
MAX_PENDING_ORDERS = 2


def _fail(db: Session, status_code: int, code: str, message: str) -> NoReturn:
    db.rollback()  # oslobodi sve zaključane redove
    raise AppError(status_code, code, message)


def _return_stock(db: Session, ticket_type_id: uuid.UUID, quantity: int) -> None:
    db.execute(
        update(TicketType)
        .where(TicketType.id == ticket_type_id)
        .values(sold_quantity=TicketType.sold_quantity - quantity)
        .execution_options(synchronize_session=False)
    )


def release_expired(db: Session) -> None:
    """Vrati karte iz rezervacija kojima je isteklo vrijeme."""
    expired = db.scalars(
        select(Order)
        .where(Order.status == OrderStatus.pending, Order.expires_at < utcnow())
        .with_for_update(skip_locked=True)
    ).all()
    for order in expired:
        order.status = OrderStatus.expired
        _return_stock(db, order.ticket_type_id, order.quantity)
    db.commit()


def reserve_tickets(
    db: Session, user_id: uuid.UUID, ticket_type_id: uuid.UUID, quantity: int
) -> Order:
    release_expired(db)

    # zaključaj korisnika: njegove istovremene rezervacije idu jedna po jedna
    db.execute(select(User.id).where(User.id == user_id).with_for_update())

    pending = db.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.user_id == user_id, Order.status == OrderStatus.pending)
    )
    if pending >= MAX_PENDING_ORDERS:
        _fail(
            db, 409, "TOO_MANY_PENDING_ORDERS",
            f"Možeš imati najviše {MAX_PENDING_ORDERS} neplaćene porudžbine",
        )

    ticket_type = db.scalar(
        select(TicketType)
        .options(joinedload(TicketType.event))
        .where(TicketType.id == ticket_type_id)
    )
    if ticket_type is None:
        _fail(db, 404, "TICKET_TYPE_NOT_FOUND", "Tip karte ne postoji")
    if ticket_type.event.starts_at <= utcnow():
        _fail(db, 409, "EVENT_STARTED", "Događaj je već počeo")

    price_cents = ticket_type.price_cents

    # ključni potez: atomski "smanji zalihu samo ako ima dovoljno"
    result = db.execute(
        update(TicketType)
        .where(
            TicketType.id == ticket_type_id,
            TicketType.sold_quantity + quantity <= TicketType.total_quantity,
        )
        .values(sold_quantity=TicketType.sold_quantity + quantity)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        _fail(db, 409, "NOT_ENOUGH_TICKETS", "Nema dovoljno slobodnih karata")

    order = Order(
        user_id=user_id,
        ticket_type_id=ticket_type_id,
        quantity=quantity,
        total_cents=price_cents * quantity,
        status=OrderStatus.pending,
        expires_at=utcnow() + timedelta(minutes=RESERVATION_MINUTES),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def cancel_order(db: Session, order_id: uuid.UUID, user_id: uuid.UUID) -> Order:
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

    order.status = OrderStatus.cancelled
    _return_stock(db, order.ticket_type_id, order.quantity)
    db.commit()
    db.refresh(order)
    return order