import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import stripe
from test_orders import auth, buyer, make_user, reserve, setup_event

from app.config import settings
from app.models.event import TicketType
from app.models.order import Order
from app.models.payment import Payment, PaymentStatus
from app.models.ticket import Ticket

WHSEC = "whsec_test_secret"


@pytest.fixture
def stripe_mock(monkeypatch):
    calls = SimpleNamespace(intents=[], refunds=[])

    def fake_create(**kwargs):
        calls.intents.append(kwargs)
        intent_id = "pi_" + kwargs["metadata"]["order_id"]
        return SimpleNamespace(id=intent_id, client_secret=intent_id + "_secret")

    def fake_refund(**kwargs):
        calls.refunds.append(kwargs)
        return SimpleNamespace(id="re_test")

    monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)
    monkeypatch.setattr(stripe.Refund, "create", fake_refund)
    monkeypatch.setattr(settings, "stripe_webhook_secret", WHSEC)
    return calls


def sign(payload: bytes, secret: str = WHSEC) -> str:
    ts = int(time.time())
    digest = hmac.new(
        secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={digest}"


def send_event(client, event_id, event_type, intent_id, amount, signature=None):
    payload = json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": event_type,
            "data": {
                "object": {
                    "id": intent_id,
                    "object": "payment_intent",
                    "amount": amount,
                    "currency": "eur",
                }
            },
        }
    ).encode()
    return client.post(
        "/webhooks/stripe",
        content=payload,
        headers={
            "Stripe-Signature": signature or sign(payload),
            "Content-Type": "application/json",
        },
    )


def pay(client, headers, order_id):
    return client.post(f"/orders/{order_id}/pay", headers=headers)


def new_paid_ready_order(client, db_session, total=100, quantity=2):
    """Napravi događaj, rezerviši karte i kreiraj plaćanje."""
    _, type_id = setup_event(client, db_session, total=total)
    h = buyer(client, db_session)
    order_id = reserve(client, h, type_id, quantity).json()["id"]
    pi = pay(client, h, order_id).json()["payment_intent_id"]
    return h, type_id, order_id, pi


def order_status(client, headers, order_id):
    return client.get(f"/orders/{order_id}", headers=headers).json()["status"]


def expire_all_orders(db_session):
    db_session.query(Order).update(
        {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}
    )
    db_session.commit()


# ---------- KREIRANJE PLAĆANJA ----------

def test_pay_requires_login(client, db_session, stripe_mock):
    r = client.post(f"/orders/{'0' * 8}-0000-0000-0000-{'0' * 12}/pay")
    assert r.status_code == 401


def test_pay_creates_payment_intent(client, db_session, stripe_mock):
    _, type_id = setup_event(client, db_session)
    h = buyer(client, db_session)
    order_id = reserve(client, h, type_id, quantity=2).json()["id"]
    r = pay(client, h, order_id)
    assert r.status_code == 200
    body = r.json()
    assert body["amount_cents"] == 9000
    assert body["currency"] == "eur"
    assert body["client_secret"].endswith("_secret")
    sent = stripe_mock.intents[0]
    assert sent["amount"] == 9000
    assert sent["metadata"]["order_id"] == order_id
    assert sent["idempotency_key"] == f"order-{order_id}"


def test_pay_twice_keeps_single_payment(client, db_session, stripe_mock):
    _, type_id = setup_event(client, db_session)
    h = buyer(client, db_session)
    order_id = reserve(client, h, type_id).json()["id"]
    first = pay(client, h, order_id).json()
    second = pay(client, h, order_id).json()
    assert first["payment_intent_id"] == second["payment_intent_id"]
    assert db_session.query(Payment).count() == 1


def test_cannot_pay_someone_elses_order(client, db_session, stripe_mock):
    _, type_id = setup_event(client, db_session)
    a = buyer(client, db_session, "a@gmail.com")
    b = buyer(client, db_session, "b@gmail.com")
    order_id = reserve(client, a, type_id).json()["id"]
    assert pay(client, b, order_id).status_code == 404


def test_cannot_pay_cancelled_order(client, db_session, stripe_mock):
    _, type_id = setup_event(client, db_session)
    h = buyer(client, db_session)
    order_id = reserve(client, h, type_id).json()["id"]
    client.post(f"/orders/{order_id}/cancel", headers=h)
    r = pay(client, h, order_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ORDER_NOT_PENDING"


def test_amount_below_minimum_rejected(client, db_session, stripe_mock):
    _, type_id = setup_event(client, db_session)
    h = buyer(client, db_session)
    db_session.query(TicketType).update({"price_cents": 10})
    db_session.commit()
    order_id = reserve(client, h, type_id).json()["id"]
    r = pay(client, h, order_id)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AMOUNT_TOO_SMALL"
    assert stripe_mock.intents == []


# ---------- POTPIS WEBHOOKA ----------

def test_webhook_without_signature_rejected(client, stripe_mock):
    r = client.post("/webhooks/stripe", content=b"{}")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_SIGNATURE"


def test_webhook_with_wrong_signature_rejected(client, db_session, stripe_mock):
    h, _, order_id, pi = new_paid_ready_order(client, db_session)
    forged = sign(b"nesto", secret="whsec_napadac")
    r = send_event(client, "evt_1", "payment_intent.succeeded", pi, 9000, forged)
    assert r.status_code == 400
    assert order_status(client, h, order_id) == "pending"


def test_webhook_disabled_without_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "")
    r = client.post("/webhooks/stripe", content=b"{}")
    assert r.status_code == 503


# ---------- USPJEŠNO PLAĆANJE ----------

def test_successful_payment_issues_tickets(client, db_session, stripe_mock):
    h, _, order_id, pi = new_paid_ready_order(client, db_session, quantity=2)
    r = send_event(client, "evt_1", "payment_intent.succeeded", pi, 9000)
    assert r.status_code == 200
    assert order_status(client, h, order_id) == "paid"
    tickets = client.get("/tickets", headers=h).json()
    assert len(tickets) == 2
    assert all(t["status"] == "valid" for t in tickets)


def test_duplicate_event_does_not_duplicate_tickets(client, db_session, stripe_mock):
    h, _, _, pi = new_paid_ready_order(client, db_session, quantity=2)
    send_event(client, "evt_1", "payment_intent.succeeded", pi, 9000)
    r = send_event(client, "evt_1", "payment_intent.succeeded", pi, 9000)
    assert r.status_code == 200
    assert db_session.query(Ticket).count() == 2


def test_second_event_for_same_payment_is_ignored(client, db_session, stripe_mock):
    h, _, _, pi = new_paid_ready_order(client, db_session, quantity=2)
    send_event(client, "evt_1", "payment_intent.succeeded", pi, 9000)
    send_event(client, "evt_2", "payment_intent.succeeded", pi, 9000)
    assert db_session.query(Ticket).count() == 2
    assert stripe_mock.refunds == []


def test_unknown_payment_intent_is_ignored(client, db_session, stripe_mock):
    r = send_event(client, "evt_1", "payment_intent.succeeded", "pi_nepoznat", 9000)
    assert r.status_code == 200
    assert db_session.query(Ticket).count() == 0


def test_wrong_amount_gets_refunded(client, db_session, stripe_mock):
    h, _, order_id, pi = new_paid_ready_order(client, db_session, quantity=2)
    send_event(client, "evt_1", "payment_intent.succeeded", pi, 100)
    assert db_session.query(Ticket).count() == 0
    assert len(stripe_mock.refunds) == 1
    assert order_status(client, h, order_id) == "cancelled"


# ---------- NEUSPJELO PLAĆANJE ----------

def test_failed_payment_keeps_order_pending(client, db_session, stripe_mock):
    h, _, order_id, pi = new_paid_ready_order(client, db_session)
    send_event(client, "evt_1", "payment_intent.payment_failed", pi, 9000)
    db_session.expire_all()
    assert db_session.query(Payment).one().status == PaymentStatus.failed
    assert order_status(client, h, order_id) == "pending"


# ---------- PLAĆANJE POSLIJE ISTEKA REZERVACIJE ----------

def test_payment_after_expiry_reclaims_tickets(client, db_session, stripe_mock):
    h, _, order_id, pi = new_paid_ready_order(client, db_session, quantity=2)
    expire_all_orders(db_session)
    assert order_status(client, h, order_id) == "expired"  # lazy release

    send_event(client, "evt_1", "payment_intent.succeeded", pi, 9000)
    assert order_status(client, h, order_id) == "paid"
    assert db_session.query(Ticket).count() == 2
    assert stripe_mock.refunds == []


def test_payment_after_expiry_refunds_if_sold_out(client, db_session, stripe_mock):
    h, type_id, order_id, pi = new_paid_ready_order(
        client, db_session, total=4, quantity=4
    )
    expire_all_orders(db_session)
    other = buyer(client, db_session, "drugi@gmail.com")
    assert reserve(client, other, type_id, quantity=4).status_code == 201

    send_event(client, "evt_1", "payment_intent.succeeded", pi, 18000)
    assert db_session.query(Ticket).count() == 0
    assert len(stripe_mock.refunds) == 1
    assert stripe_mock.refunds[0]["payment_intent"] == pi
    assert order_status(client, h, order_id) == "expired"