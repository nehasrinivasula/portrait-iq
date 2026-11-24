"""The API rejects the same free-text inputs the UI does (D-9)."""
import secrets

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import SecretStr  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def creds(monkeypatch):
    c = {"username": f"demo-{secrets.token_hex(4)}", "password": secrets.token_urlsafe(24)}
    monkeypatch.setattr(settings, "demo_auth_username", c["username"])
    monkeypatch.setattr(settings, "demo_auth_password", SecretStr(c["password"]))
    monkeypatch.setattr(settings, "auth_token_secret", SecretStr(secrets.token_urlsafe(32)))
    monkeypatch.setattr(settings, "auth_token_ttl_seconds", 3600)
    return c


@pytest.fixture()
def client(engine):
    Session = sessionmaker(bind=engine)

    def override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def headers(client, creds):
    token = client.post("/api/auth/login", json=creds).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# Login -----------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {"username": "", "password": "x"},
    {"username": "   ", "password": "x"},
    {"username": "a", "password": ""},
    {"username": "u" * 129, "password": "x"},
    {"username": "a", "password": "p" * 257},
    {"username": "a\nb", "password": "x"},
])
def test_login_rejects_malformed_input_before_checking_credentials(client, creds, body):
    assert client.post("/api/auth/login", json=body).status_code == 422


def test_login_trims_the_username(client, creds):
    r = client.post("/api/auth/login", json={"username": f"  {creds['username']} ", "password": creds["password"]})
    assert r.status_code == 200


# Audit filters -----------------------------------------------------------------

@pytest.mark.parametrize("param", ["actor", "session_id", "action"])
def test_audit_filters_reject_control_characters(client, headers, param):
    r = client.get("/api/audit", headers=headers, params={param: "bad\x00value"})
    assert r.status_code == 422
    assert "control or invisible" in r.json()["detail"]


@pytest.mark.parametrize("param", ["actor", "session_id", "action"])
def test_audit_filters_reject_overlong_values(client, headers, param):
    assert client.get("/api/audit", headers=headers, params={param: "x" * 129}).status_code == 422


def test_audit_filters_trim_whitespace(client, headers):
    assert client.get("/api/audit", headers=headers, params={"actor": "  someone  "}).status_code == 200


# Target IP ---------------------------------------------------------------------

@pytest.mark.parametrize("ip", ["", "256.1.1.1", "1.2.3", "010.0.0.1", "example.com", "1.2.3.4;ls"])
def test_job_start_rejects_invalid_target_ip(client, headers, ip):
    r = client.post("/api/jobs", headers=headers, params={"target_ip": ip}, json={"tools": ["no-such-tool"]})
    assert r.status_code == 422
    assert "IP address" in r.json()["detail"] or "IPv4" in r.json()["detail"]


def test_job_start_accepts_valid_ip_then_reports_the_unknown_tool(client, headers):
    r = client.post("/api/jobs?target_ip=192.0.2.10", headers=headers, json={"tools": ["no-such-tool"]})
    assert r.status_code == 400
    assert "IP" not in r.json()["detail"]


# Log filters + import ----------------------------------------------------------

def test_log_search_rejects_overlong_text(client, headers):
    r = client.get("/api/logs/events", headers=headers, params={"q": "x" * 201})
    assert r.status_code == 422


def test_search_accepts_tabs_and_line_breaks_from_pasted_log_text(client, headers):
    for text in ["nak\treply", "line one\nline two", "a\r\nb"]:
        assert client.get("/api/logs/events", headers=headers, params={"q": text}).status_code == 200


def test_search_still_refuses_other_control_characters(client, headers):
    r = client.get("/api/logs/events", headers=headers, params={"q": "a\x00b"})
    assert r.status_code == 422 and "control or invisible" in r.json()["detail"]


def test_passwords_keep_their_leading_and_trailing_spaces(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_auth_username", "spacey")
    monkeypatch.setattr(settings, "demo_auth_password", SecretStr("  pass phrase  "))
    monkeypatch.setattr(settings, "auth_token_secret", SecretStr(secrets.token_urlsafe(32)))
    monkeypatch.setattr(settings, "auth_token_ttl_seconds", 3600)
    assert client.post("/api/auth/login", json={"username": "spacey", "password": "  pass phrase  "}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "spacey", "password": "pass phrase"}).status_code == 401


def test_import_rejects_unsupported_and_slashed_names(client, headers):
    bad = client.post("/api/sources/local/upload", headers=headers, files={"files": ("run.exe", b"x")})
    assert bad.status_code == 400
    assert "not a supported log file" in bad.json()["detail"]
