"""Focused coverage for the local-demo login and route protection contract."""
import secrets

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import settings
from app.main import app


@pytest.fixture()
def demo_credentials(monkeypatch):
    credentials = {
        "username": f"demo-{secrets.token_hex(4)}",
        "password": secrets.token_urlsafe(24),
    }
    monkeypatch.setattr(settings, "demo_auth_username", credentials["username"])
    monkeypatch.setattr(settings, "demo_auth_password", SecretStr(credentials["password"]))
    monkeypatch.setattr(settings, "auth_token_secret", SecretStr(secrets.token_urlsafe(32)))
    monkeypatch.setattr(settings, "auth_token_ttl_seconds", 3600)
    return credentials


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, credentials: dict[str, str]) -> str:
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200
    return response.json()["access_token"]


def test_valid_login_and_me(client: TestClient, demo_credentials):
    token = login(client, demo_credentials)
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["username"] == demo_credentials["username"]


def test_invalid_login_is_rejected(client: TestClient, demo_credentials):
    response = client.post(
        "/api/auth/login",
        json={"username": demo_credentials["username"], "password": secrets.token_urlsafe(24)},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password.", "code": "invalid_credentials"}


def test_protected_route_rejects_missing_token(client: TestClient, demo_credentials):
    response = client.get("/api/devices")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required.", "code": "auth_required"}


def test_authenticated_protected_route_is_allowed(client: TestClient, demo_credentials):
    token = login(client, demo_credentials)
    response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_health_is_public(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
