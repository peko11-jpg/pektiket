from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.qr import verify_qr_token
from app.models.event import Event, TicketType, utcnow
from app.models.ticket import Ticket, TicketStatus
from app.models.user import Role


def validate_ticket(db: Session, scanner, token: str) -> dict:
    ticket_id = verify_qr_token(token)
    if ticket_id is None:
        raise AppError(400, "INVALID_QR", "QR kod nije ispravan")

    row = db.execute(
        select(Ticket, TicketType.name, Event.title, Event.organizer_id)
        .join(TicketType, Ticket.ticket_type_id == TicketType.id)
        .join(Event, TicketType.event_id == Event.id)
        .where(Ticket.id == ticket_id)
    ).first()
    if row is None:
        raise AppError(404, "TICKET_NOT_FOUND", "Karta ne postoji")

    ticket, type_name, event_title, organizer_id = row

    if scanner.role != Role.admin and organizer_id != scanner.id:
        raise AppError(403, "FORBIDDEN", "Ova karta nije za tvoj događaj")

    if ticket.status == TicketStatus.used:
        raise AppError(409, "TICKET_ALREADY_USED", "Karta je već iskorišćena")

    # atomski: označi kao iskorišćenu samo ako je još važeća
    now = utcnow()
    result = db.execute(
        update(Ticket)
        .where(Ticket.id == ticket_id, Ticket.status == TicketStatus.valid)
        .values(status=TicketStatus.used, used_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:  # neko je bio brži
        db.rollback()
        raise AppError(409, "TICKET_ALREADY_USED", "Karta je već iskorišćena")
    db.commit()

    return {
        "ticket_id": ticket_id,
        "event_title": event_title,
        "ticket_type": type_name,
        "used_at": now,
    }