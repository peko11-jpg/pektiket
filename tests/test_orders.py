import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.core.errors import AppError
from app.core.security import hash_password
from app.models.event import Event, TicketType
from app.models.order import Order
from app.models.user import Role, User
from app.services.orders import reserve_tickets


def make_user(db_session, email, role=Role.user):
    db_session.add(
        User(email=email, password_hash=hash_password("lozinka123"), role=role)
    )
    db_session.commit()


def auth(client, email):
    r = client.post("/auth/login", json={"email": email, "password": "lozinka123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def future(days=30):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def setup_event(client, db_session, total=100):
    make_user(db_session, "org@gmail.com", Role.organizer)
    org = auth(client, "org@gmail.com")
    event_id = client.post(
        "/events",
        json={"title": "Koncert", "venue": "Arena", "starts_at": future()},
        headers=org,
    ).json()["id"]
    type_id = client.post(
        f"/events/{event_id}/ticket-types",
        json={"name": "VIP", "price_cents": 4500, "total_quantity": total},
        headers=org,
    ).json()["id"]
    return event_id, type_id


def buyer(client, db_session, email="kupac@gmail.com"):
    make_user(db_session, email)
    return auth(client, email)


def reserve(client, headers, type_id, quantity=1):
    return client.post(
        "/orders",
        json={"ticket_type_id": type_id, "quantity": quantity},
        headers=headers,
    )


def available(client, event_id):
    return client.get(f"/events/{event_id}").json()["ticket_types"][0]["available"]


# ---------- REZERVACIJA ----------

def test_reserve_requires_login(client, db_session):
    _, type_id = setup_event(client, db_session)
    r = client.post("/orders", json={"ticket_type_id": type_id, "quantity": 1})
    assert r.status_code == 401


def test_reserve_success(client, db_session):
    event_id, type_id = setup_event(client, db_session)
    r = reserve(client, buyer(client, db_session), type_id, quantity=2)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    assert body["total_cents"] == 9000
    assert available(client, event_id) == 98


def test_reserve_more_than_available(client, db_session):
    event_id, type_id = setup_event(client, db_session, total=3)
    r = reserve(client, buyer(client, db_session), type_id, quantity=4)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "NOT_ENOUGH_TICKETS"
    assert available(client, event_id) == 3


def test_reserve_invalid_quantity(client, db_session):
    _, type_id = setup_event(client, db_session)
    h = buyer(client, db_session)
    assert reserve(client, h, type_id, quantity=0).status_code == 422
    assert reserve(client, h, type_id, quantity=5).status_code == 422


def test_reserve_unknown_ticket_type(client, db_session):
    setup_event(client, db_session)
    r = reserve(client, buyer(client, db_session), str(uuid.uuid4()))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "TICKET_TYPE_NOT_FOUND"


def test_cannot_buy_for_started_event(client, db_session):
    _, type_id = setup_event(client, db_session)
    h = buyer(client, db_session)
    db_session.query(Event).update(
        {"starts_at": datetime.now(timezone.utc) - timedelta(hours=1)}
    )
    db_session.commit()
    r = reserve(client, h, type_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EVENT_STARTED"


def test_max_pending_orders_per_user(client, db_session):
    _, type_id = setup_event(client, db_session)
    h = buyer(client, db_session)
    assert reserve(client, h, type_id).status_code == 201
    assert reserve(client, h, type_id).status_code == 201
    r = reserve(client, h, type_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TOO_MANY_PENDING_ORDERS"


# ---------- OTKAZIVANJE I ISTEK ----------

def test_cancel_returns_tickets(client, db_session):
    event_id, type_id = setup_event(client, db_session)
    h = buyer(client, db_session)
    order_id = reserve(client, h, type_id, quantity=2).json()["id"]
    r = client.post(f"/orders/{order_id}/cancel", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert available(client, event_id) == 100


def test_cannot_cancel_someone_elses_order(client, db_session):
    _, type_id = setup_event(client, db_session)
    a = buyer(client, db_session, "a@gmail.com")
    b = buyer(client, db_session, "b@gmail.com")
    order_id = reserve(client, a, type_id).json()["id"]
    r = client.post(f"/orders/{order_id}/cancel", headers=b)
    assert r.status_code == 404


def test_cannot_cancel_twice(client, db_session):
    event_id, type_id = setup_event(client, db_session)
    h = buyer(client, db_session)
    order_id = reserve(client, h, type_id).json()["id"]
    client.post(f"/orders/{order_id}/cancel", headers=h)
    r = client.post(f"/orders/{order_id}/cancel", headers=h)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ORDER_NOT_PENDING"
    assert available(client, event_id) == 100  # nije vraćeno dvaput


def test_expired_reservation_is_released(client, db_session):
    _, type_id = setup_event(client, db_session, total=4)
    a = buyer(client, db_session, "a@gmail.com")
    b = buyer(client, db_session, "b@gmail.com")
    order_id = reserve(client, a, type_id, quantity=4).json()["id"]
    assert reserve(client, b, type_id).status_code == 409  # sve zauzeto

    db_session.query(Order).update(
        {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}
    )
    db_session.commit()

    assert reserve(client, b, type_id).status_code == 201  # karte su opet slobodne
    r = client.get(f"/orders/{order_id}", headers=a)
    assert r.json()["status"] == "expired"


def test_list_shows_only_own_orders(client, db_session):
    _, type_id = setup_event(client, db_session)
    a = buyer(client, db_session, "a@gmail.com")
    b = buyer(client, db_session, "b@gmail.com")
    reserve(client, a, type_id)
    assert len(client.get("/orders", headers=a).json()) == 1
    assert len(client.get("/orders", headers=b).json()) == 0


# ---------- KONKURENTNOST (najvažniji test) ----------

def test_concurrent_purchases_never_oversell(client, db_session, session_factory):
    _, type_id = setup_event(client, db_session, total=5)

    for i in range(20):
        db_session.add(
            User(email=f"buyer{i}@gmail.com", password_hash="x", role=Role.user)
        )
    db_session.commit()
    user_ids = [
        u.id for u in db_session.query(User).filter(User.email.like("buyer%")).all()
    ]

    def buy(user_id):
        with session_factory() as s:
            try:
                reserve_tickets(s, user_id, uuid.UUID(type_id), 1)
                return "ok"
            except AppError as e:
                return e.code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(buy, user_ids))

    assert results.count("ok") == 5
    assert results.count("NOT_ENOUGH_TICKETS") == 15

    db_session.expire_all()
    ticket_type = db_session.get(TicketType, uuid.UUID(type_id))
    assert ticket_type.sold_quantity == 5