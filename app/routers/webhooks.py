import json

import stripe
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.db import get_db
from app.services.payments import process_stripe_event

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    secret = settings.stripe_webhook_secret
    if not secret:  # prazan secret bi dozvolio lažne poruke
        raise AppError(503, "WEBHOOK_NOT_CONFIGURED", "Webhook nije podešen")

    payload = await request.body()  # sirovi bajtovi, potpis se računa nad njima
    signature = request.headers.get("stripe-signature", "")
    try:
        stripe.Webhook.construct_event(payload, signature, secret)
    except (ValueError, stripe.SignatureVerificationError):
        raise AppError(400, "INVALID_SIGNATURE", "Neispravan potpis")

    await run_in_threadpool(process_stripe_event, db, json.loads(payload))
    return {"received": True}