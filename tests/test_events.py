import uuid
from datetime import datetime, timedelta, timezone

from app.core.security import hash_password
from app.models.user import Role, User


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


def create_event(client, headers, **overrides):
    payload = {"title": "Koncert", "venue": "Arena", "starts_at": future()}
    payload.update(overrides)
    return client.post("/events", json=payload, headers=headers)


def organizer(client, db_session, email="org@gmail.com"):
    make_user(db_session, email, Role.organizer)
    return auth(client, email)


# ---------- KREIRANJE ----------

def test_create_event_requires_login(client):
    r = client.post("/events", json={"title": "x", "venue": "y", "starts_at": future()})
    assert r.status_code == 401


def test_regular_user_cannot_create_event(client, db_session):
    make_user(db_session, "obican@gmail.com")
    r = create_event(client, auth(client, "obican@gmail.com"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_organizer_can_create_event(client, db_session):
    r = create_event(client, organizer(client, db_session))
    assert r.status_code == 201
    assert r.json()["title"] == "Koncert"
    assert r.json()["ticket_types"] == []


def test_event_in_past_rejected(client, db_session):
    r = create_event(client, organizer(client, db_session), starts_at=future(-1))
    assert r.status_code == 422


def test_event_start_without_timezone_rejected(client, db_session):
    r = create_event(
        client, organizer(client, db_session), starts_at="2030-01-01T20:00:00"
    )
    assert r.status_code == 422


# ---------- ČITANJE ----------

def test_list_events_is_public_and_paginated(client, db_session):
    h = organizer(client, db_session)
    for i in range(3):
        create_event(client, h, title=f"Događaj {i}")
    r = client.get("/events?limit=2")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3


def test_get_unknown_event(client):
    r = client.get(f"/events/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "EVENT_NOT_FOUND"


def test_invalid_event_id(client):
    r = client.get("/events/nije-uuid")
    assert r.status_code == 422


# ---------- IZMJENA I BRISANJE ----------

def test_other_organizer_cannot_edit(client, db_session):
    owner = organizer(client, db_session, "a@gmail.com")
    other = organizer(client, db_session, "b@gmail.com")
    event_id = create_event(client, owner).json()["id"]
    r = client.patch(f"/events/{event_id}", json={"title": "Hakovano"}, headers=other)
    assert r.status_code == 403


def test_owner_can_update(client, db_session):
    h = organizer(client, db_session)
    event_id = create_event(client, h).json()["id"]
    r = client.patch(f"/events/{event_id}", json={"title": "Novi naziv"}, headers=h)
    assert r.status_code == 200
    assert r.json()["title"] == "Novi naziv"
    assert r.json()["venue"] == "Arena"  # ostalo nije diran


def test_admin_can_update_any_event(client, db_session):
    h = organizer(client, db_session)
    event_id = create_event(client, h).json()["id"]
    make_user(db_session, "admin@gmail.com", Role.admin)
    r = client.patch(
        f"/events/{event_id}",
        json={"venue": "Druga sala"},
        headers=auth(client, "admin@gmail.com"),
    )
    assert r.status_code == 200


def test_update_title_null_rejected(client, db_session):
    h = organizer(client, db_session)
    event_id = create_event(client, h).json()["id"]
    r = client.patch(f"/events/{event_id}", json={"title": None}, headers=h)
    assert r.status_code == 422


def test_owner_can_delete(client, db_session):
    h = organizer(client, db_session)
    event_id = create_event(client, h).json()["id"]
    assert client.delete(f"/events/{event_id}", headers=h).status_code == 204
    assert client.get(f"/events/{event_id}").status_code == 404


# ---------- TIPOVI KARATA ----------

def add_type(client, headers, event_id, **overrides):
    payload = {"name": "VIP", "price_cents": 4500, "total_quantity": 100}
    payload.update(overrides)
    return client.post(f"/events/{event_id}/ticket-types", json=payload, headers=headers)


def test_add_ticket_type(client, db_session):
    h = organizer(client, db_session)
    event_id = create_event(client, h).json()["id"]
    r = add_type(client, h, event_id)
    assert r.status_code == 201
    assert r.json()["available"] == 100


def test_ticket_type_invalid_values(client, db_session):
    h = organizer(client, db_session)
    event_id = create_event(client, h).json()["id"]
    assert add_type(client, h, event_id, price_cents=-1).status_code == 422
    assert add_type(client, h, event_id, total_quantity=0).status_code == 422


def test_duplicate_ticket_type_name(client, db_session):
    h = organizer(client, db_session)
    event_id = create_event(client, h).json()["id"]
    add_type(client, h, event_id)
    r = add_type(client, h, event_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TICKET_TYPE_EXISTS"


def test_ticket_type_shows_in_event(client, db_session):
    h = organizer(client, db_session)
    event_id = create_event(client, h).json()["id"]
    add_type(client, h, event_id)
    r = client.get(f"/events/{event_id}")
    assert len(r.json()["ticket_types"]) == 1
    assert r.json()["ticket_types"][0]["name"] == "VIP"


def test_other_user_cannot_add_ticket_type(client, db_session):
    owner = organizer(client, db_session, "a@gmail.com")
    other = organizer(client, db_session, "b@gmail.com")
    event_id = create_event(client, owner).json()["id"]
    assert add_type(client, other, event_id).status_code == 403