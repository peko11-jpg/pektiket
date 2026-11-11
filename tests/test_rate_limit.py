import pytest

from app.config import settings
from app.core.rate_limit import redis_client


def _clear_keys():
    for k in redis_client.scan_iter("rl:*"):
        redis_client.delete(k)


@pytest.fixture
def limiter_on(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    _clear_keys()
    yield
    _clear_keys()


def test_login_blocked_after_too_many_attempts(client, limiter_on):
    body = {"email": "meta@gmail.com", "password": "pogresna123"}
    for _ in range(5):
        assert client.post("/auth/login", json=body).status_code == 401
    r = client.post("/auth/login", json=body)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert int(r.headers["Retry-After"]) > 0


def test_register_blocked_after_too_many_requests(client, limiter_on):
    for i in range(5):
        r = client.post(
            "/auth/register",
            json={"email": f"user{i}@gmail.com", "password": "lozinka123"},
        )
        assert r.status_code == 201
    r = client.post(
        "/auth/register",
        json={"email": "user99@gmail.com", "password": "lozinka123"},
    )
    assert r.status_code == 429