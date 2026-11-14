import hashlib
import hmac
import io
import uuid

import qrcode

from app.config import settings

# prefiks da potpis za QR ne može da se zloupotrijebi drugdje (JWT koristi isti ključ)
_CONTEXT = b"pektiket-ticket-qr:"


def _sign(ticket_id: uuid.UUID) -> str:
    digest = hmac.new(
        settings.secret_key.encode(), _CONTEXT + ticket_id.bytes, hashlib.sha256
    ).hexdigest()
    return digest[:32]


def make_qr_token(ticket_id: uuid.UUID) -> str:
    return f"{ticket_id}.{_sign(ticket_id)}"


def verify_qr_token(token: str) -> uuid.UUID | None:
    """Vrati ID karte ako je potpis pravi, inače None."""
    try:
        raw_id, signature = token.strip().split(".", 1)
        ticket_id = uuid.UUID(raw_id)
    except ValueError:
        return None
    if not hmac.compare_digest(signature.encode(), _sign(ticket_id).encode()):
        return None
    return ticket_id


def make_qr_png(data: str) -> bytes:
    image = qrcode.make(data, box_size=8, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()