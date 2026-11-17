"""The cleanup and sign-out endpoints: auth, the prompt's data, the chosen action and its audit."""
import secrets

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import SecretStr  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.collectors import k8s  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditLogEntry, Job  # noqa: E402
from tests.test_cleanup import FakeRunner, pod  # noqa: E402


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


@pytest.fixture()
def runner(monkeypatch):
    r = FakeRunner([pod("piq-tshark-aaaa", "adhoc")])
    monkeypatch.setattr(k8s, "get_pod_runner", lambda: r)
    return r


def audit_actions(db):
    return [e.action for e in db.query(AuditLogEntry).order_by(AuditLogEntry.id).all()]


def test_the_endpoints_need_a_signed_in_user(client, runner):
    assert client.get("/api/cleanup").status_code == 401
    body = {"action": "remove", "context": "logout", "items": ["pod:piq-tshark-aaaa"]}
    assert client.post("/api/cleanup", json=body).status_code == 401
    assert runner.deleted == []


def test_status_lists_leftovers_per_run(client, headers, runner, db):
    db.add(Job(id="job-1", status="interrupted", mode="live", tools="[]"))
    db.commit()
    runner.pods["piq-rti-bbbb"] = pod("piq-rti-bbbb", "job-1", "rti")
    body = client.get("/api/cleanup", headers=headers).json()
    assert body["state"] == "pending" and body["pending_count"] == 2 and body["interrupted_runs"] == 1
    by_job = {g["job_id"]: g for g in body["groups"]}
    assert by_job["job-1"]["status"] == "interrupted"
    assert by_job["adhoc"]["items"][0]["id"] == "pod:piq-tshark-aaaa"


def test_remove_carries_out_the_choice_and_audits_it(client, headers, runner, engine):
    r = client.post("/api/cleanup", headers=headers,
                    json={"action": "remove", "context": "restart", "items": ["pod:piq-tshark-aaaa"]})
    assert r.status_code == 200
    body = r.json()
    assert body["outcomes"] == [{"id": "pod:piq-tshark-aaaa", "result": "removed", "reason": None}]
    assert body["status"]["state"] == "clean"
    assert runner.deleted == ["piq-tshark-aaaa"]
    with sessionmaker(bind=engine)() as db:
        entry = db.query(AuditLogEntry).filter_by(action="cleanup_choice").one()
        assert "context=restart action=remove removed=pod:piq-tshark-aaaa" in entry.detail
        assert "state=clean" in entry.detail and entry.session_id


def test_keep_leaves_the_pod_and_reports_preserved(client, headers, runner):
    r = client.post("/api/cleanup", headers=headers,
                    json={"action": "keep", "context": "logout", "items": ["pod:piq-tshark-aaaa"]})
    assert r.json()["status"]["state"] == "preserved" and runner.deleted == []
    assert client.get("/api/cleanup", headers=headers).json()["pending_count"] == 0


@pytest.mark.parametrize("body,code", [
    ({"action": "remove", "context": "logout", "items": []}, 422),
    ({"action": "nuke", "context": "logout", "items": ["pod:a"]}, 422),
    ({"action": "remove", "context": "elsewhere", "items": ["pod:a"]}, 422),
    ({"action": "remove", "context": "logout", "items": ["files:../../etc"]}, 400),
    ({"action": "remove", "context": "logout", "items": ["pod:a b"]}, 400),
])
def test_bad_requests_are_refused_and_delete_nothing(client, headers, runner, body, code):
    assert client.post("/api/cleanup", headers=headers, json=body).status_code == code
    assert runner.deleted == []


def test_logout_ends_the_session_on_the_server_and_is_audited(client, headers, engine):
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 423      # the old token no longer works
    with sessionmaker(bind=engine)() as db:
        assert "logout" in audit_actions(db)


def test_logout_without_a_token_is_refused(client):
    assert client.post("/api/auth/logout").status_code == 401
