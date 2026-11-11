from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.rate_limit import hit, rate_limit
from app.core.security import create_access_token, hash_password, verify_password
from app.db import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenOut
from app.schemas.user import UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# lažni hash: da provjera traje isto i kad email ne postoji
DUMMY_HASH = hash_password("dummy-password-123")


@router.post(
    "/register",
    response_model=UserOut,
    status_code=201,
    dependencies=[Depends(rate_limit("register", 5, 3600))],
)
def register(data: UserCreate, db: Session = Depends(get_db)):
    email = data.email.lower()

    if db.scalar(select(User).where(User.email == email)):
        raise AppError(409, "EMAIL_TAKEN", "Nalog sa ovim emailom već postoji")

    user = User(email=email, password_hash=hash_password(data.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # dvije registracije u istom trenutku
        db.rollback()
        raise AppError(409, "EMAIL_TAKEN", "Nalog sa ovim emailom već postoji")

    db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=TokenOut,
    dependencies=[Depends(rate_limit("login", 10, 60))],
)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    hit(f"rl:login-email:{data.email.lower()}", 5, 300)

    user = db.scalar(select(User).where(User.email == data.email.lower()))

    if user is None:
        verify_password(data.password, DUMMY_HASH)
        raise AppError(401, "INVALID_CREDENTIALS", "Pogrešan email ili lozinka")

    if not verify_password(data.password, user.password_hash):
        raise AppError(401, "INVALID_CREDENTIALS", "Pogrešan email ili lozinka")

    if not user.is_active:
        raise AppError(403, "ACCOUNT_DISABLED", "Nalog je deaktiviran")

    return TokenOut(access_token=create_access_token(str(user.id)))