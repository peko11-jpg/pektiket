import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.ticket import TicketStatus


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    ticket_type_id: uuid.UUID
    status: TicketStatus
    created_at: datetime
    qr_token: str


class TicketValidateRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class TicketValidationOut(BaseModel):
    ticket_id: uuid.UUID
    event_title: str
    ticket_type: str
    used_at: datetime