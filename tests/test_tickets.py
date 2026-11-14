import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

from test_orders import auth, buyer, make_user, setup_event

from app.core.errors import AppError
from app.core.qr import make_qr_token, verify_qr_token
from app.models.event import utcnow
from app.models.order import Order, OrderStatus
from app.models.ticket import Ticket
from app.models.user import Role, User
from app.services.tickets import validate_ticket


def issue_ticket(db_session, type_id, email):
    """Napravi plaćenu porudžbinu i kartu direktno u bazi."""
    owner = db_session.query(User).filter(User.email == email).one()
    order = Order(
        user_id=owner.id,
        ticket_type_id=uuid.UUID(type_id),
        quantity=1,
        total_cents=4500,
        status=OrderStatus.paid,
        expires_at=utcnow() + timedelta(minutes=10),
    )
    db_session.add(order)
    db_session.flush()
    ticket = Ticket(
        order_id=order.id, ticket_type_id=order.ticket_type_id, owner_id=owner.id
    )
    db_session.add(ticket)
    db_session.commit()
    return ticket.id


def ticket_setup(client, db_session):
    _, type_id = setup_event(client, db_session)  # organizer: org@gmail.com
    buyer_h = buyer(client, db_session, "kupac@gmail.com")
    ticket_id = issue_ticket(db_session, type_id, "kupac@gmail.com")
    org_h = auth(client, "org@gmail.com")
    return ticket_id, buyer_h, org_h


def scan(client, headers, token):
    return client.post("/tickets/validate", json={"token": token}, headers=headers)


# ---------- QR KOD ----------

def test_qr_requires_login(client):
    r = client.get(f"/tickets/{uuid.uuid4()}/qr")
    assert r.status_code == 401


def test_qr_image_for_owner(client, db_session):
    ticket_id, buyer_h, _ = ticket_setup(client, db_session)
    r = client.get(f"/tickets/{ticket_id}/qr", headers=buyer_h)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_qr_hidden_from_other_users(client, db_session):
    ticket_id, _, _ = ticket_setup(client, db_session)
    other = buyer(client, db_session, "drugi@gmail.com")
    r = client.get(f"/tickets/{ticket_id}/qr", headers=other)
    assert r.status_code == 404


def test_ticket_list_contains_valid_token(client, db_session):
    ticket_id, buyer_h, _ = ticket_setup(client, db_session)
    tickets = client.get("/tickets", headers=buyer_h).json()
    assert verify_qr_token(tickets[0]["qr_token"]) == ticket_id


def test_token_verification_rejects_fakes():
    tid = uuid.uuid4()
    good = make_qr_token(tid)
    assert verify_qr_token(good) == tid

    raw, sig = good.split(".")
    assert verify_qr_token(f"{raw}.{'0' * 32}") is None  # pogrešan potpis
    assert verify_qr_token(f"{uuid.uuid4()}.{sig}") is None  # tuđi ID, stari potpis
    assert verify_qr_token("smece") is None
    assert verify_qr_token(f"{raw}.šđčćž") is None
    assert verify_qr_token("") is None


# ---------- SKENIRANJE NA ULAZU ----------

def test_validate_requires_login(client):
    r = client.post("/tickets/validate", json={"token": "x" * 20})
    assert r.status_code == 401


def test_regular_user_cannot_validate(client, db_session):
    ticket_id, buyer_h, _ = ticket_setup(client, db_session)
    r = scan(client, buyer_h, make_qr_token(ticket_id))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_organizer_validates_ticket(client, db_session):
    ticket_id, buyer_h, org_h = ticket_setup(client, db_session)
    r = scan(client, org_h, make_qr_token(ticket_id))
    assert r.status_code == 200
    assert r.json()["event_title"] == "Koncert"
    assert r.json()["ticket_type"] == "VIP"
    tickets = client.get("/tickets", headers=buyer_h).json()
    assert tickets[0]["status"] == "used"


def test_second_scan_rejected(client, db_session):
    ticket_id, _, org_h = ticket_setup(client, db_session)
    token = make_qr_token(ticket_id)
    assert scan(client, org_h, token).status_code == 200
    r = scan(client, org_h, token)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TICKET_ALREADY_USED"


def test_forged_token_rejected(client, db_session):
    ticket_id, buyer_h, org_h = ticket_setup(client, db_session)
    forged = f"{ticket_id}.{'0' * 32}"
    r = scan(client, org_h, forged)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_QR"
    r = scan(client, org_h, "ovo-nije-pravi-token-123")
    assert r.status_code == 400
    # karta je i dalje važeća
    assert client.get("/tickets", headers=buyer_h).json()[0]["status"] == "valid"


def test_valid_signature_unknown_ticket(client, db_session):
    _, _, org_h = ticket_setup(client, db_session)
    r = scan(client, org_h, make_qr_token(uuid.uuid4()))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "TICKET_NOT_FOUND"


def test_other_organizer_cannot_validate(client, db_session):
    ticket_id, buyer_h, _ = ticket_setup(client, db_session)
    make_user(db_session, "org2@gmail.com", Role.organizer)
    other_org = auth(client, "org2@gmail.com")
    r = scan(client, other_org, make_qr_token(ticket_id))
    assert r.status_code == 403
    assert client.get("/tickets", headers=buyer_h).json()[0]["status"] == "valid"


def test_admin_can_validate_any_ticket(client, db_session):
    ticket_id, _, _ = ticket_setup(client, db_session)
    make_user(db_session, "admin@gmail.com", Role.admin)
    admin = auth(client, "admin@gmail.com")
    r = scan(client, admin, make_qr_token(ticket_id))
    assert r.status_code == 200


# ---------- ISTOVREMENO SKENIRANJE ----------

def test_concurrent_scans_accept_only_one(client, db_session, session_factory):
    ticket_id, _, _ = ticket_setup(client, db_session)
    token = make_qr_token(ticket_id)
    org = db_session.query(User).filter(User.email == "org@gmail.com").one()
    scanner = SimpleNamespace(id=org.id, role=org.role)

    def do_scan(_):
        with session_factory() as s:
            try:
                validate_ticket(s, scanner, token)
                return "ok"
            except AppError as e:
                return e.code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(do_scan, range(10)))

    assert results.count("ok") == 1
    assert results.count("TICKET_ALREADY_USED") == 9    