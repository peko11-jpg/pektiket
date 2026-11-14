import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_roles
from app.core.errors import AppError
from app.core.qr import make_qr_png, make_qr_token
from app.core.rate_limit import rate_limit
from app.db import get_db
from app.models.ticket import Ticket
from app.models.user import Role, User
from app.schemas.ticket import TicketOut, TicketValidateRequest, TicketValidationOut
from app.services.tickets import validate_ticket

router = APIRouter(prefix="/tickets", tags=["tickets"])

can_scan = require_roles(Role.organizer, Role.admin)


@router.get("", response_model=list[TicketOut])
def my_tickets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tickets = db.scalars(
        select(Ticket)
        .where(Ticket.owner_id == user.id)
        .order_by(Ticket.created_at.desc())
        .limit(100)
    ).all()
    return [
        TicketOut(
            id=t.id,
            order_id=t.order_id,
            ticket_type_id=t.ticket_type_id,
            status=t.status,
            created_at=t.created_at,
            qr_token=make_qr_token(t.id),
        )
        for t in tickets
    ]


@router.get("/{ticket_id}/qr")
def ticket_qr(
    ticket_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ticket = db.scalar(
        select(Ticket).where(Ticket.id == ticket_id, Ticket.owner_id == user.id)
    )
    if ticket is None:
        raise AppError(404, "TICKET_NOT_FOUND", "Karta ne postoji")
    png = make_qr_png(make_qr_token(ticket.id))
    return Response(content=png, media_type="image/png")


@router.post(
    "/validate",
    response_model=TicketValidationOut,
    dependencies=[Depends(rate_limit("validate", 120, 60))],
)
def validate(
    data: TicketValidateRequest,
    db: Session = Depends(get_db),
    scanner: User = Depends(can_scan),
):
    return validate_ticket(db, scanner, data.token)