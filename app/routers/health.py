from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.rate_limit import redis_client
from app.db import get_db

router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("select 1"))
        redis_client.ping()
    except Exception:
        raise AppError(503, "UNHEALTHY", "Servis nije spreman")
    return {"status": "ok"}