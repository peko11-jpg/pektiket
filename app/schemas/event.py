import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


def check_start(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError(
            "Vrijeme mora imati vremensku zonu, npr. 2030-06-01T20:00:00+02:00"
        )
    if v <= datetime.now(timezone.utc):
        raise ValueError("Događaj mora biti u budućnosti")
    return v


class TicketTypeCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    price_cents: int = Field(ge=0, le=100_000_000)
    total_quantity: int = Field(gt=0, le=1_000_000)


class TicketTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price_cents: int
    total_quantity: int
    available: int


class EventCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    venue: str = Field(min_length=2, max_length=200)
    starts_at: datetime

    @field_validator("starts_at")
    @classmethod
    def starts_in_future(cls, v: datetime) -> datetime:
        return check_start(v)


class EventUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    venue: str | None = Field(default=None, min_length=2, max_length=200)
    starts_at: datetime | None = None

    @field_validator("title", "venue", "starts_at")
    @classmethod
    def not_null(cls, v):
        if v is None:
            raise ValueError("Polje ne smije biti null")
        return v

    @field_validator("starts_at")
    @classmethod
    def starts_in_future(cls, v: datetime) -> datetime:
        return check_start(v)


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organizer_id: uuid.UUID
    title: str
    description: str | None
    venue: str
    starts_at: datetime
    created_at: datetime
    ticket_types: list[TicketTypeOut]


class EventPage(BaseModel):
    items: list[EventOut]
    total: int
    limit: int
    offset: int