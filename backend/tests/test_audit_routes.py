"""Focused API coverage for the SCRUM-36 compliance timeline."""
from datetime import datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.audit import log_action
from app.auth import User, get_current_user
from app.collectors.base import ResourceLimits
from app import db as dbmodule
from app.db import Base, get_db
from app.main import app
from app.models import AuditLogEntry
from app.orchestrator.jobs import create_job, run_job
from app.config import settings


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id="reviewer", name="Reviewer", session_id="review-session"
    )
    monkeypatch.setattr("app.auth.is_locked", lambda _user_id: False)
    seed = Session()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def _add(db, *, actor="engineer", session_id="session-a", action="connect", detail="mode=live", seconds=0):
    entry = AuditLogEntry(
        timestamp=datetime(2026, 11, 2, 10, 0, 0) + timedelta(seconds=seconds),
        actor=actor,
        session_id=session_id,
        action=action,
        detail=detail,
    )
    db.add(entry)
    db.commit()
    return entry


def test_audit_timeline_is_protected(env):
    client, _ = env
    app.dependency_overrides.pop(get_current_user)

    response = client.get("/api/audit")

    assert response.status_code == 401
    assert response.json()["code"] == "auth_required"


def test_audit_timeline_filters_one_session_and_is_chronological(env):
    client, db = env
    first = _add(db, action="login", detail="outcome=success", seconds=0)
    _add(db, session_id="other-session", action="job_start", seconds=1)
    third = _add(db, action="logs_search", detail="filters=['severity'] matched=3", seconds=2)

    response = client.get("/api/audit", params={"session_id": "session-a"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [first.id, third.id]
    assert [item["action"] for item in body["items"]] == ["login", "logs_search"]
    assert body["items"][0]["session_id"] == "session-a"
    assert body["items"][0]["timestamp"].endswith("Z")

    audit_view = db.scalars(select(AuditLogEntry).where(AuditLogEntry.action == "audit_view")).one()
    assert audit_view.actor == "reviewer"
    assert audit_view.session_id == "review-session"
    assert "session_id" in audit_view.detail and "session-a" not in audit_view.detail


def test_audit_timeline_paginates_and_rejects_invalid_time_range(env):
    client, db = env
    _add(db, action="login", seconds=0)
    second = _add(db, action="connect", seconds=1)
    _add(db, action="job_start", seconds=2)

    response = client.get("/api/audit", params={"limit": 1, "offset": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["limit"] == 1 and body["offset"] == 1
    assert body["items"][0]["id"] == second.id

    invalid = client.get(
        "/api/audit",
        params={"since": "2026-11-02T11:00:00Z", "until": "2026-11-02T10:00:00Z"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "until must be after since"


def test_audit_timeline_normalizes_offset_times_and_filters_actor_and_action(env):
    client, db = env
    _add(db, actor="alex", action="login", seconds=0)
    matching = _add(db, actor="alex", action="connect", seconds=1)
    _add(db, actor="blair", action="connect", seconds=2)
    _add(db, actor="alex", action="connect", seconds=3)

    # 04:00:00.500-06:00 is 10:00:00.500 UTC. The naive end is UTC too.
    # This deliberately mixes aware and naive input and must not be treated
    # as local UTC wall-clock time or raise a TypeError.
    response = client.get(
        "/api/audit",
        params={
            "actor": "alex",
            "action": "connect",
            "since": "2026-11-02T04:00:00.500-06:00",
            "until": "2026-11-02T10:00:03",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [matching.id]


def test_audit_timeline_rejects_equivalent_mixed_timezone_range(env):
    client, _ = env

    response = client.get(
        "/api/audit",
        params={
            "since": "2026-11-02T10:00:00Z",
            "until": "2026-11-02T04:00:00-06:00",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "until must be after since"


def test_init_db_adds_audit_columns_without_losing_existing_rows(tmp_path, monkeypatch):
    """The narrow compatibility upgrade preserves development rows in place."""
    old_engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with old_engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE audit_log (
                id INTEGER NOT NULL PRIMARY KEY,
                timestamp DATETIME NOT NULL,
                actor VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                detail TEXT
            )
        """))
        connection.execute(text("""
            CREATE TABLE jobs (
                id VARCHAR NOT NULL PRIMARY KEY,
                mode VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                current_step VARCHAR,
                cpu_limit_pct INTEGER,
                mem_limit_mb INTEGER,
                max_output_mb INTEGER,
                tools TEXT,
                created_at DATETIME,
                finished_at DATETIME
            )
        """))
        connection.execute(text(
            "INSERT INTO audit_log (id, timestamp, actor, action, detail) "
            "VALUES (1, '2026-11-02 10:00:00', 'engineer', 'connect', 'mode=live')"
        ))
        connection.execute(text(
            "INSERT INTO jobs (id, mode, status) VALUES ('job-old', 'live', 'done')"
        ))

    monkeypatch.setattr(dbmodule, "engine", old_engine)
    dbmodule.init_db()

    inspector = inspect(old_engine)
    assert "session_id" in {column["name"] for column in inspector.get_columns("audit_log")}
    assert {"requested_by", "session_id"} <= {
        column["name"] for column in inspector.get_columns("jobs")
    }
    with old_engine.connect() as connection:
        assert connection.execute(text("SELECT actor, action FROM audit_log")).one() == (
            "engineer", "connect"
        )
        assert connection.execute(text("SELECT id, status FROM jobs")).one() == ("job-old", "done")


def test_log_action_records_the_request_session(db):
    log_action(db, "engineer", "upload", "kind=pcap", session_id="session-123")
    entry = db.scalars(select(AuditLogEntry)).one()
    assert entry.actor == "engineer"
    assert entry.session_id == "session-123"


def test_login_starts_an_auditable_session(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(settings, "demo_auth_username", "audit-demo")
    monkeypatch.setattr(settings, "demo_auth_password", SecretStr("demo-password"))
    monkeypatch.setattr(settings, "auth_token_secret", SecretStr("demo-signing-secret"))
    app.dependency_overrides.pop(get_current_user)

    login = client.post("/api/auth/login", json={"username": "audit-demo", "password": "demo-password"})
    assert login.status_code == 200

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200

    response = client.get("/api/audit", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert response.status_code == 200
    entries = response.json()["items"]
    assert [entry["action"] for entry in entries] == ["login", "session_check"]
    assert all(entry["actor"] == "audit-demo" for entry in entries)
    assert entries[0]["description"] == "outcome=success"
    assert entries[0]["session_id"] == entries[1]["session_id"]
    assert "password" not in entries[0]["description"]


def test_failed_login_is_audited_without_submitted_credentials(env, monkeypatch):
    client, db = env
    monkeypatch.setattr(settings, "demo_auth_username", "audit-demo")
    monkeypatch.setattr(settings, "demo_auth_password", SecretStr("demo-password"))
    monkeypatch.setattr(settings, "auth_token_secret", SecretStr("demo-signing-secret"))

    response = client.post("/api/auth/login", json={"username": "private-name", "password": "private-password"})

    assert response.status_code == 401
    entry = db.scalars(select(AuditLogEntry).where(AuditLogEntry.action == "login")).one()
    assert entry.actor == "anonymous"
    assert entry.session_id is None
    assert entry.detail == "outcome=failed"


def test_background_job_status_and_steps_keep_the_starting_session(db):
    job = create_job(
        db,
        mode="live",
        tools=[],
        limits=ResourceLimits(cpu_pct=50, mem_mb=1024, max_output_mb=2048),
        requested_by="engineer",
        session_id="session-job",
    )

    run_job(db, job, "192.0.2.10", plugins=[])

    entries = list(db.scalars(
        select(AuditLogEntry).where(AuditLogEntry.session_id == "session-job")
        .order_by(AuditLogEntry.id)
    ))
    assert entries[0].action == "job_status" and entries[0].detail.endswith("status=running")
    assert [entry.detail for entry in entries if entry.action == "job_step"] == [
        "job=" + job.id + " step=" + step
        for step in ("deploy_tools", "collect_server_logs", "collect_network_diagnostics",
                     "collect_grafana_health", "cleanup", "package")
    ]
    assert entries[-1].action == "job_status" and entries[-1].detail.endswith("status=done")
    assert all(entry.actor == "engineer" for entry in entries)
