import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.order import OrderStatus


class OrderCreate(BaseModel):
    ticket_type_id: uuid.UUID
    quantity: int = Field(ge=1, le=4)


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_type_id: uuid.UUID
    quantity: int
    total_cents: int
    status: OrderStatus
    expires_at: datetime
    created_at: datetime