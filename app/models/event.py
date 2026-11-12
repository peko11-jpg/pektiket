import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organizer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    venue: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ticket_types: Mapped[list["TicketType"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="TicketType.created_at",
    )


class TicketType(Base):
    __tablename__ = "ticket_types"
    __table_args__ = (
        UniqueConstraint("event_id", "name", name="uq_ticket_type_event_name"),
        CheckConstraint("price_cents >= 0", name="ck_price_not_negative"),
        CheckConstraint("total_quantity > 0", name="ck_total_positive"),
        CheckConstraint(
            "sold_quantity >= 0 AND sold_quantity <= total_quantity",
            name="ck_sold_in_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    price_cents: Mapped[int] = mapped_column(Integer)
    total_quantity: Mapped[int] = mapped_column(Integer)
    sold_quantity: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    event: Mapped["Event"] = relationship(back_populates="ticket_types")

    @property
    def available(self) -> int:
        return self.total_quantity - self.sold_quantity