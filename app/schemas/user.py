import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from app.models.user import Role


class UserCreate(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Lozinka mora imati najmanje 8 karaktera")
        if len(v) > 128:
            raise ValueError("Lozinka je predugačka")
        if not any(c.isdigit() for c in v) or not any(c.isalpha() for c in v):
            raise ValueError("Lozinka mora sadržati slova i brojeve")
        return v


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: Role
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)