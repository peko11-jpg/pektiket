import uuid

from pydantic import BaseModel


class PaymentOut(BaseModel):
    order_id: uuid.UUID
    payment_intent_id: str
    client_secret: str
    amount_cents: int
    currency: str