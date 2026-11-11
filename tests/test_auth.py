from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings
from app.models.user import User


def register(client, email="test@gmail.com", password="lozinka123"):
    return client.post("/auth/register", json={"email": email, "password": password})


def login(client, email="test@gmail.com", password="lozinka123"):
    return client.post("/auth/login", json={"email": email, "password": password})


# ---------- REGISTRACIJA ----------

def test_register_success(client):
    r = register(client)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "test@gmail.com"
    assert "password" not in body
    assert "password_hash" not in body


def test_register_duplicate_email(client):
    register(client)
    r = register(client)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_TAKEN"


def test_register_email_is_case_insensitive(client):
    register(client, email="Test@Gmail.com")
    r = register(client, email="test@gmail.com")
    assert r.status_code == 409


def test_register_weak_password(client):
    r = register(client, password="abc")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_register_invalid_email(client):
    r = register(client, email="nije-email")
    assert r.status_code == 422


def test_register_cannot_choose_role(client):
    r = client.post(
        "/auth/register",
        json={"email": "hack@gmail.com", "password": "lozinka123", "role": "admin"},
    )
    assert r.status_code == 201
    assert r.json()["role"] == "user"


def test_password_is_hashed_in_database(client, db_session):
    register(client)
    user = db_session.query(User).first()
    assert user.password_hash.startswith("$argon2")
    assert "lozinka123" not in user.password_hash


# ---------- LOGIN ----------

def test_login_success(client):
    register(client)
    r = login(client)
    assert r.status_code == 200
    assert r.json()["token_type"] == "bearer"
    assert r.json()["access_token"]


def test_login_wrong_password(client):
    register(client)
    r = login(client, password="pogresna123")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_unknown_email_gives_same_error(client):
    r = login(client, email="nepostoji@gmail.com")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_disabled_account(client, db_session):
    register(client)
    user = db_session.query(User).first()
    user.is_active = False
    db_session.commit()
    r = login(client)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ACCOUNT_DISABLED"


# ---------- ZAŠTIĆENI ENDPOINT ----------

def test_me_with_valid_token(client):
    register(client)
    token = login(client).json()["access_token"]
    r = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "test@gmail.com"


def test_me_without_token(client):
    r = client.get("/users/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "NOT_AUTHENTICATED"


def test_me_with_garbage_token(client):
    r = client.get("/users/me", headers={"Authorization": "Bearer smece"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"


def test_me_with_expired_token(client):
    user_id = register(client).json()["id"]
    expired = jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.secret_key,
        algorithm="HS256",
    )
    r = client.get("/users/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"


def test_me_with_token_signed_by_wrong_key(client):
    user_id = register(client).json()["id"]
    forged = jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "tudja-tajna-od-napadaca-0123456789",
        algorithm="HS256",
    )
    r = client.get("/users/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401