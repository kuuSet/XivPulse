"""Server authentication HTTP contract tests."""

from __future__ import annotations

import app.api.auth as auth_api
import pytest
from app.security import SESSION_COOKIE, AuthSettings, clear_login_failures
from argon2 import PasswordHasher
from fastapi.testclient import TestClient


def test_enabled_auth_protects_api_and_manages_the_session_cookie(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AuthSettings(
        enabled=True,
        username="reader",
        password_hash=PasswordHasher().hash("correct-password"),
        session_secret="test-session-secret-with-at-least-32-bytes",
        secure_cookies=False,
        trust_proxy=False,
    )
    monkeypatch.setattr(auth_api, "auth_settings", lambda: settings)
    clear_login_failures("testclient")

    assert client.get("/api/settings").status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "reader", "password": "wrong-password"},
        ).status_code
        == 401
    )

    login = client.post(
        "/api/auth/login",
        json={"username": "reader", "password": "correct-password"},
    )
    assert login.status_code == 200
    assert login.json() == {"enabled": True, "authenticated": True, "username": "reader"}
    assert "HttpOnly" in login.headers["set-cookie"]
    assert SESSION_COOKIE in client.cookies
    assert client.get("/api/settings").status_code == 200

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert SESSION_COOKIE not in client.cookies
    assert client.get("/api/settings").status_code == 401
