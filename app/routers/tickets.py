from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db import get_db
from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.ticket import TicketOut

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=list[TicketOut])
def my_tickets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.scalars(
        select(Ticket)
        .where(Ticket.owner_id == user.id)
        .order_by(Ticket.created_at.desc())
        .limit(100)
    ).all()