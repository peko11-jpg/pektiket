import uuid

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import decode_access_token
from app.db import get_db
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise AppError(401, "NOT_AUTHENTICATED", "Potrebna je prijava")

    user_id = decode_access_token(creds.credentials)
    if user_id is None:
        raise AppError(401, "INVALID_TOKEN", "Token je neispravan ili je istekao")

    try:
        user = db.get(User, uuid.UUID(user_id))
    except ValueError:
        raise AppError(401, "INVALID_TOKEN", "Token je neispravan ili je istekao")

    if user is None or not user.is_active:
        raise AppError(401, "INVALID_TOKEN", "Token je neispravan ili je istekao")

    return user