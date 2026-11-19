"""Rate limits: wrong-password lockout per email and per IP, public auth cap, and the API-wide cap."""

import pytest

from app.config import settings
from app.core import rate_limit
from app.core.rate_limit import LOGIN_FAILURES_PER_EMAIL, LOGIN_FAILURES_PER_IP, PUBLIC_AUTH_PER_IP, REQUESTS_PER_IP

PASSWORD = "x"  # conftest's users all have this password


@pytest.fixture(autouse=True)
def limits_on(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    rate_limit.reset_all()
    yield
    rate_limit.reset_all()


def login(client, email, password, ip="203.0.113.7"):
    return client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
        headers={"X-Forwarded-For": ip},
    )


def test_an_email_is_locked_after_too_many_wrong_passwords_even_with_the_right_one(client, users):
    for _ in range(LOGIN_FAILURES_PER_EMAIL.hits):
        assert login(client, "ceo@optiminastic.com", "wrong").status_code == 401
    locked = login(client, "ceo@optiminastic.com", PASSWORD)
    assert locked.status_code == 429
    assert "Too many attempts" in locked.json()["detail"]
    assert int(locked.headers["Retry-After"]) > 0


def test_the_lock_is_per_email_and_case_insensitive(client, users):
    for _ in range(LOGIN_FAILURES_PER_EMAIL.hits):
        login(client, " CEO@optiminastic.com", "wrong", ip=f"198.51.100.{_}")
    assert login(client, "ceo@optiminastic.com", PASSWORD, ip="192.0.2.1").status_code == 429
    assert login(client, "cfo@optiminastic.com", PASSWORD, ip="192.0.2.1").status_code == 200


def test_a_right_password_clears_the_count(client, users):
    for _ in range(LOGIN_FAILURES_PER_EMAIL.hits - 1):
        login(client, "ceo@optiminastic.com", "wrong")
    assert login(client, "ceo@optiminastic.com", PASSWORD).status_code == 200
    for _ in range(LOGIN_FAILURES_PER_EMAIL.hits - 1):
        assert login(client, "ceo@optiminastic.com", "wrong").status_code == 401
    assert login(client, "ceo@optiminastic.com", PASSWORD).status_code == 200


def test_one_ip_guessing_many_emails_is_stopped(client, users):
    for i in range(LOGIN_FAILURES_PER_IP.hits):
        assert login(client, f"guess{i}@optiminastic.com", "wrong").status_code == 401
    assert login(client, "ceo@optiminastic.com", PASSWORD).status_code == 429
    assert login(client, "ceo@optiminastic.com", PASSWORD, ip="203.0.113.99").status_code == 200


def test_a_forged_forwarded_for_does_not_dodge_the_ip_limit(client, users):
    # The proxy appends the real address last; a caller can only add entries in front of it.
    for i in range(LOGIN_FAILURES_PER_IP.hits):
        login(client, f"guess{i}@optiminastic.com", "wrong", ip=f"10.0.0.{i}, 203.0.113.7")
    assert login(client, "ceo@optiminastic.com", PASSWORD, ip="10.9.9.9, 203.0.113.7").status_code == 429


def test_public_auth_endpoints_are_capped_per_ip(client):
    for _ in range(PUBLIC_AUTH_PER_IP.hits):
        assert client.get("/api/auth/invite/nope", headers={"X-Forwarded-For": "203.0.113.8"}).status_code == 404
    capped = client.get("/api/auth/invite/nope", headers={"X-Forwarded-For": "203.0.113.8"})
    assert capped.status_code == 429


def test_the_whole_api_is_capped_per_ip_and_keeps_cors_headers(client):
    headers = {"X-Forwarded-For": "203.0.113.9", "Origin": "http://testserver"}
    for _ in range(REQUESTS_PER_IP.hits):
        assert client.get("/health", headers=headers).status_code == 200
    capped = client.get("/health", headers=headers)
    assert capped.status_code == 429
    assert capped.headers.get("access-control-allow-origin") == "http://testserver"
    assert client.get("/health", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 200


def test_turned_off_nothing_is_limited(client, users, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    for _ in range(LOGIN_FAILURES_PER_EMAIL.hits + 2):
        login(client, "ceo@optiminastic.com", "wrong")
    assert login(client, "ceo@optiminastic.com", PASSWORD).status_code == 200
