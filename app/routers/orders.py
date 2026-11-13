import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.errors import AppError
from app.core.rate_limit import rate_limit
from app.db import get_db
from app.models.order import Order
from app.models.user import User
from app.schemas.order import OrderCreate, OrderOut
from app.services.orders import cancel_order, release_expired, reserve_tickets
from app.schemas.payment import PaymentOut
from app.services.payments import create_payment

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "",
    response_model=OrderOut,
    status_code=201,
    dependencies=[Depends(rate_limit("orders", 10, 60))],
)
def create_order(
    data: OrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return reserve_tickets(db, user.id, data.ticket_type_id, data.quantity)


@router.get("", response_model=list[OrderOut])
def my_orders(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    user_id = user.id
    release_expired(db)
    return db.scalars(
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(50)
    ).all()


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user_id = user.id
    release_expired(db)
    order = db.scalar(
        select(Order).where(Order.id == order_id, Order.user_id == user_id)
    )
    if order is None:
        raise AppError(404, "ORDER_NOT_FOUND", "Porudžbina ne postoji")
    return order


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return cancel_order(db, order_id, user.id)

@router.post(
    "/{order_id}/pay",
    response_model=PaymentOut,
    dependencies=[Depends(rate_limit("pay", 10, 60))],
)
def pay(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payment, client_secret = create_payment(db, user.id, order_id)
    return PaymentOut(
        order_id=payment.order_id,
        payment_intent_id=payment.stripe_payment_intent_id,
        client_secret=client_secret,
        amount_cents=payment.amount_cents,
        currency=payment.currency,
    )