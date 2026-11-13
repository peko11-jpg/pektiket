import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.ticket import TicketStatus


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    ticket_type_id: uuid.UUID
    status: TicketStatus
    created_at: datetime