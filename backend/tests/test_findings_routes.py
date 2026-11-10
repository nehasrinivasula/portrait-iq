"""/api/jobs/{id}/findings, /findings/run and /findings/coverage through FastAPI's TestClient."""
from datetime import datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import User, get_current_user
from app.db import Base, get_db
from app.main import app
from app.models import AuditLogEntry, Job, NormalizedEvent

T0 = datetime(2024, 6, 2, 10, 0, 0)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr("app.auth.is_locked", lambda *_: False)
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
    app.dependency_overrides[get_current_user] = lambda: User(id="tester", name="Tester")
    seed = Session()
    seed.add(Job(id="job-1", mode="live", status="done"))
    for i in range(1, 8):
        seed.add(NormalizedEvent(
            timestamp=T0 + timedelta(minutes=i), source="portrait", severity="ERROR", device_id="hub-a",
            message="Request/Response Timeout observed during the ping scheduler execution",
            job_id="job-1", source_file="a.csv", source_line=i))
    seed.add(NormalizedEvent(timestamp=T0, source="portrait", severity="WARN", device_id="hub-b",
                             message="!DDS DISCSimpleParticipantDiscoveryPluginReaderListener_onSampleLost: ff0101c7",
                             job_id="job-1", source_file="a.csv", source_line=9))
    seed.add(NormalizedEvent(timestamp=T0, source="portrait", severity="ERROR", device_id="hub-b",
                             message="Compressor stalled", job_id="job-1", source_file="a.csv", source_line=10))
    seed.commit()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def actions(db):
    return [a for (a,) in db.execute(select(AuditLogEntry.action)).all()]


def test_findings_and_coverage_refuse_a_locked_session(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr("app.auth.is_locked", lambda *_: True)
    assert client.get("/api/jobs/job-1/findings").status_code == 423
    assert client.get("/api/jobs/job-1/findings/coverage").status_code == 423
    assert client.post("/api/jobs/job-1/findings/run").status_code == 423


def test_findings_are_empty_until_the_rules_have_run(env):
    client, _ = env
    assert client.get("/api/jobs/job-1/findings").json() == []


def test_running_the_rules_returns_findings_with_evidence(env):
    client, db = env
    response = client.post("/api/jobs/job-1/findings/run")
    assert response.status_code == 200
    findings = {f["rule_id"]: f for f in response.json()}
    assert set(findings) == {"ping_timeout", "dds_security"}
    ping = findings["ping_timeout"]
    assert ping["count"] == 7 and ping["status"] == "unconfirmed" and ping["actions"]
    assert ping["first_seen"].startswith("2024-06-02T10:01:00") and ping["last_seen"].startswith("2024-06-02T10:07:00")
    assert ping["devices"] == [{"device_id": "hub-a", "count": 7}] and ping["devices_total"] == 1
    assert len(ping["evidence"]) == 7
    first = ping["evidence"][0]
    assert first["source_file"] == "a.csv" and first["source_line"] == 1 and "ping scheduler" in first["message"]
    again = {f["rule_id"]: f for f in client.get("/api/jobs/job-1/findings").json()}
    assert again == findings
    assert "findings_run" in actions(db) and "findings_view" in actions(db)


def test_running_twice_does_not_duplicate_findings(env):
    client, _ = env
    client.post("/api/jobs/job-1/findings/run")
    client.post("/api/jobs/job-1/findings/run")
    assert len(client.get("/api/jobs/job-1/findings").json()) == 2


def test_an_unknown_collection_is_a_404_for_run_and_coverage(env):
    client, _ = env
    assert client.get("/api/jobs/nope/findings").status_code == 404
    assert client.post("/api/jobs/nope/findings/run").status_code == 404
    assert client.get("/api/jobs/nope/findings/coverage").status_code == 404


def test_coverage_names_the_errors_nothing_explains(env):
    client, db = env
    body = client.get("/api/jobs/job-1/findings/coverage").json()
    assert body["events"] == 9 and body["problem_events"] == 9
    assert body["problem_unmatched"] == 1 and body["per_rule"] == {"ping_timeout": 7, "dds_security": 1}
    assert body["unmatched"][0]["example"] == "Compressor stalled" and body["unmatched"][0]["count"] == 1
    assert "findings_coverage_view" in actions(db)
    assert client.get("/api/jobs/job-1/findings/coverage?top=0").status_code == 422
