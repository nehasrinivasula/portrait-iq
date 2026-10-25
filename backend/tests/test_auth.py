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


def test_login_not_configured_returns_503(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "demo_auth_username", None)
    response = client.post("/api/auth/login", json={"username": "a", "password": "b"})

    assert response.status_code == 503
    assert response.json()["code"] == "not_configured"


def test_tampered_token_is_rejected(client: TestClient, demo_credentials):
    token = login(client, demo_credentials)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered}"})

    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


def test_expired_token_is_rejected(client: TestClient, demo_credentials, monkeypatch):
    monkeypatch.setattr(settings, "auth_token_ttl_seconds", -10)
    token = login(client, demo_credentials)
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


# Idle lock (S-2): starting jobs and uploading count as activity -------------

def test_jobs_and_uploads_keep_the_session_unlocked(client: TestClient, demo_credentials, monkeypatch):
    from app import auth
    clock = [1_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    headers = {"Authorization": f"Bearer {login(client, demo_credentials)}"}
    idle = settings.session_idle_timeout_seconds

    def upload():   # a refused file: still an action the lock guards
        return client.post("/api/uploads?kind=pcap", headers=headers,
                           files={"file": ("x.pcap", b"not a capture")}).status_code

    def start_job():
        return client.post("/api/jobs?target_ip=192.0.2.10", headers=headers,
                           json={"tools": ["no-such-tool"]}).status_code

    clock[0] += idle - 10
    assert upload() == 400
    clock[0] += idle - 10      # longer than the timeout since login, not since the upload
    assert start_job() == 400

    clock[0] += idle + 1       # really idle; polling a job does not count
    assert client.get("/api/jobs/no-such-job", headers=headers).status_code == 404
    assert upload() == 423
    assert start_job() == 423

    login(client, demo_credentials)   # logging in again unlocks
    assert upload() == 400
