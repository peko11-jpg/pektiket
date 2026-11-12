import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_roles
from app.core.errors import AppError
from app.db import get_db
from app.models.event import Event, TicketType
from app.models.user import Role, User
from app.schemas.event import (
    EventCreate, EventOut, EventPage, EventUpdate, TicketTypeCreate, TicketTypeOut,
)

router = APIRouter(prefix="/events", tags=["events"])

can_manage_events = require_roles(Role.organizer, Role.admin)


def get_event_or_404(db: Session, event_id: uuid.UUID) -> Event:
    stmt = (
        select(Event)
        .options(selectinload(Event.ticket_types))
        .where(Event.id == event_id)
        .execution_options(populate_existing=True)
    )
    event = db.scalar(stmt)
    if event is None:
        raise AppError(404, "EVENT_NOT_FOUND", "Događaj ne postoji")
    return event


def ensure_owner_or_admin(user: User, event: Event) -> None:
    if user.role != Role.admin and event.organizer_id != user.id:
        raise AppError(403, "FORBIDDEN", "Ovo nije tvoj događaj")


@router.get("", response_model=EventPage)
def list_events(
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    total = db.scalar(select(func.count()).select_from(Event).where(Event.starts_at >= now))
    events = db.scalars(
        select(Event)
        .options(selectinload(Event.ticket_types))
        .where(Event.starts_at >= now)
        .order_by(Event.starts_at)
        .limit(limit)
        .offset(offset)
    ).all()
    return EventPage(items=events, total=total, limit=limit, offset=offset)


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_event_or_404(db, event_id)


@router.post("", response_model=EventOut, status_code=201)
def create_event(
    data: EventCreate,
    db: Session = Depends(get_db),
    user: User = Depends(can_manage_events),
):
    event = Event(**data.model_dump(), organizer_id=user.id)
    db.add(event)
    db.commit()
    return get_event_or_404(db, event.id)


@router.patch("/{event_id}", response_model=EventOut)
def update_event(
    event_id: uuid.UUID,
    data: EventUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(can_manage_events),
):
    event = get_event_or_404(db, event_id)
    ensure_owner_or_admin(user, event)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    db.commit()
    return get_event_or_404(db, event_id)


@router.delete("/{event_id}", status_code=204)
def delete_event(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(can_manage_events),
):
    event = get_event_or_404(db, event_id)
    ensure_owner_or_admin(user, event)
    db.delete(event)
    db.commit()


@router.post("/{event_id}/ticket-types", response_model=TicketTypeOut, status_code=201)
def add_ticket_type(
    event_id: uuid.UUID,
    data: TicketTypeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(can_manage_events),
):
    event = get_event_or_404(db, event_id)
    ensure_owner_or_admin(user, event)

    ticket_type = TicketType(**data.model_dump(), event_id=event.id)
    db.add(ticket_type)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "TICKET_TYPE_EXISTS", "Tip karte sa tim imenom već postoji")
    db.refresh(ticket_type)
    return ticket_type