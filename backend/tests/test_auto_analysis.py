"""Pattern detection after logs arrive: it runs in the background for the collection, never makes the
pull fail, records how it ended, and the status says when findings are missing, running, failed or old."""
from datetime import datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.analysis.auto as auto
from app.analysis import engine as analysis_engine
from app.analysis.engine import run_analysis
from app.analysis.findings import findings_for
from app.analysis.status import RUNNING_TOO_LONG, findings_status
from app.auth import User, get_current_user
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import AnalysisRun, AuditLogEntry, IngestedFile, Job, NormalizedEvent

T0 = datetime(2024, 6, 2, 10, 0, 0)
PING = "Request/Response Timeout observed during the ping scheduler execution"
HEADER = (
    "timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,"
    "sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description"
)


def add_events(db, job_id="job-1", n=3, message=PING):
    for i in range(n):
        db.add(NormalizedEvent(timestamp=T0 + timedelta(minutes=i), source="portrait", severity="ERROR",
                               message=message, device_id="hub-a", job_id=job_id, source_file="a.csv", source_line=i))


@pytest.fixture
def job(db):
    db.add(Job(id="job-1", mode="live", status="done"))
    db.commit()
    return db


class Outcome:
    def __init__(self, status):
        self.status = status


def test_it_runs_only_for_a_collection_whose_events_were_stored():
    assert auto.should_run("job-1", [Outcome("already_acquired"), Outcome("ingested")])
    assert not auto.should_run("job-1", [Outcome("already_acquired"), Outcome("failed"), Outcome("acquired")])
    assert not auto.should_run("job-1", [])
    assert not auto.should_run(None, [Outcome("ingested")])           # logs tied to no collection have no findings to hold


def test_the_background_run_produces_findings_and_records_itself(job, engine):
    add_events(job)
    job.commit()
    auto.run_in_background(engine, "job-1", "tester", "sess-1")
    job.expire_all()
    assert [f.rule_id for f in findings_for(job, "job-1")] == ["ping_timeout"]
    run = job.get(AnalysisRun, "job-1")
    assert run.status == "ok" and run.events_scanned == 3 and run.error is None
    audit = job.execute(select(AuditLogEntry.action, AuditLogEntry.detail, AuditLogEntry.session_id)).all()
    assert ("findings_run", "job=job-1 auto=true", "sess-1") in [tuple(a) for a in audit]


def test_a_failed_background_run_is_recorded_not_raised_and_keeps_the_old_findings(job, engine, monkeypatch):
    add_events(job)
    job.commit()
    run_analysis(job, "job-1")

    def boom(db, job_id):
        raise RuntimeError("patientName=John Smith")

    monkeypatch.setattr(auto, "run_analysis", boom)
    auto.run_in_background(engine, "job-1", "tester", None)           # must not raise
    job.expire_all()
    run = job.get(AnalysisRun, "job-1")
    assert run.status == "failed" and run.error == "RuntimeError"      # the type, never the message
    actions = [a for (a,) in job.execute(select(AuditLogEntry.action)).all()]
    assert "findings_run_failed" in actions
    assert "John" not in " ".join(d or "" for (d,) in job.execute(select(AuditLogEntry.detail)).all())
    assert len(findings_for(job, "job-1")) == 1                        # the earlier findings are still there


def test_one_collection_gets_one_lock():
    assert analysis_engine._lock_for("a") is analysis_engine._lock_for("a")
    assert analysis_engine._lock_for("a") is not analysis_engine._lock_for("b")


def test_status_before_any_run(job):
    assert findings_status(job, "job-1")["state"] == "never" and findings_status(job, "job-1")["stale"] is False
    add_events(job)
    job.commit()
    status = findings_status(job, "job-1")
    assert status["state"] == "never" and status["stale"] is True and status["last_run_at"] is None


def test_status_after_a_run_and_when_more_logs_arrive(job):
    add_events(job)
    job.commit()
    run_analysis(job, "job-1")
    status = findings_status(job, "job-1")
    assert status["state"] == "ok" and status["stale"] is False and status["events_scanned"] == 3
    assert status["last_run_at"].tzinfo is not None
    ran_at = job.get(AnalysisRun, "job-1").ran_at
    job.add(IngestedFile(source_file="b.csv", job_id="job-1", ingested_at=ran_at + timedelta(seconds=1)))
    job.commit()
    assert findings_status(job, "job-1")["stale"] is True            # logs arrived after the run
    job.get(AnalysisRun, "job-1").ran_at = ran_at + timedelta(seconds=2)
    job.commit()
    assert findings_status(job, "job-1")["stale"] is False           # a later run has seen them


def test_status_running_and_a_run_that_never_finished(job):
    now = datetime.utcnow()
    job.add(AnalysisRun(job_id="job-1", status="running", ran_at=now))
    job.commit()
    running = findings_status(job, "job-1", now=now + timedelta(minutes=1))
    assert running["state"] == "running" and running["started_at"] is not None and running["last_run_at"] is None
    dead = findings_status(job, "job-1", now=now + RUNNING_TOO_LONG + timedelta(minutes=1))
    assert dead["state"] == "failed"


def test_status_of_a_failed_run_carries_the_error_type(job):
    job.add(AnalysisRun(job_id="job-1", status="failed", ran_at=datetime.utcnow(), error="OperationalError"))
    job.commit()
    status = findings_status(job, "job-1")
    assert status["state"] == "failed" and status["error"] == "OperationalError"


@pytest.fixture
def client_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "staging_dir", str(tmp_path / "staging"))
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)

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
    seed.commit()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def _csv(message):
    return (HEADER + "\n" + ",".join(
        f'"{v}"' for v in ["2024-06-02T02:00:01.500+0000", "0", "1", "ERROR", "hub-a", "uuid-1", "unsetFriendlyName",
                           "SvcAdmDiagUI[1:0]", ":0", "General", message])).encode()


def test_uploading_logs_to_a_collection_produces_findings_without_pressing_run(client_env):
    client, db = client_env
    response = client.post("/api/sources/local/upload?job_id=job-1", files=[("files", ("logs.csv", _csv(PING)))])
    assert response.status_code == 200, response.text
    findings = client.get("/api/jobs/job-1/findings").json()
    assert [f["rule_id"] for f in findings] == ["ping_timeout"] and findings[0]["count"] == 1
    status = client.get("/api/jobs/job-1/findings/status").json()
    assert status["state"] == "ok" and status["stale"] is False and status["events_scanned"] == 1


def test_uploading_without_a_collection_runs_nothing(client_env):
    client, db = client_env
    response = client.post("/api/sources/local/upload", files=[("files", ("logs.csv", _csv(PING)))])
    assert response.status_code == 200
    assert db.query(AnalysisRun).count() == 0


def test_a_failure_in_the_background_does_not_fail_the_upload(client_env, monkeypatch):
    client, db = client_env
    monkeypatch.setattr(auto, "run_analysis", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    response = client.post("/api/sources/local/upload?job_id=job-1", files=[("files", ("logs.csv", _csv(PING)))])
    assert response.status_code == 200
    assert client.get("/api/jobs/job-1/findings/status").json()["state"] == "failed"


def test_status_of_an_unknown_collection_is_a_404(client_env):
    client, _ = client_env
    assert client.get("/api/jobs/nope/findings/status").status_code == 404


def test_a_manual_run_shows_as_running_while_it_works(job, engine, monkeypatch):
    add_events(job)
    job.commit()
    seen = []
    real_scan = analysis_engine.scan

    def watching_scan(rows, rules=None):
        with sessionmaker(bind=engine)() as other:
            seen.append(other.get(AnalysisRun, "job-1").status)
        return real_scan(rows) if rules is None else real_scan(rows, rules)

    monkeypatch.setattr(analysis_engine, "scan", watching_scan)
    run_analysis(job, "job-1")
    assert seen == ["running"] and job.get(AnalysisRun, "job-1").status == "ok"


def test_a_failed_manual_run_says_failed_not_running_and_keeps_the_old_findings(job, monkeypatch):
    add_events(job)
    job.commit()
    run_analysis(job, "job-1")

    def boom(rows, rules=None):
        raise RuntimeError("patientName=John Smith")

    monkeypatch.setattr(analysis_engine, "scan", boom)
    with pytest.raises(RuntimeError):
        run_analysis(job, "job-1")
    run = job.get(AnalysisRun, "job-1")
    assert run.status == "failed" and run.error == "RuntimeError"
    assert len(findings_for(job, "job-1")) == 1


def test_runs_left_running_by_a_dead_process_are_failed_at_startup(job):
    from app.analysis.status import recover_interrupted_analysis
    job.add(Job(id="job-2", mode="live", status="done"))
    job.add(AnalysisRun(job_id="job-1", status="running", ran_at=datetime.utcnow()))
    job.add(AnalysisRun(job_id="job-2", status="ok", ran_at=datetime.utcnow(), events_scanned=5))
    job.commit()
    assert recover_interrupted_analysis(job) == 1
    job.expire_all()
    assert job.get(AnalysisRun, "job-1").status == "failed" and job.get(AnalysisRun, "job-1").error == "Interrupted"
    assert job.get(AnalysisRun, "job-2").status == "ok"
    assert findings_status(job, "job-1")["state"] == "failed"


def test_status_refuses_a_locked_session_without_counting_as_activity(client_env, monkeypatch):
    client, _ = client_env
    touched = []
    monkeypatch.setattr("app.auth.touch_session", lambda user_id: touched.append(user_id))
    assert client.get("/api/jobs/job-1/findings/status").status_code == 200
    assert touched == []                                          # polling must not keep a session alive
    monkeypatch.setattr("app.orchestrator.routes.is_locked", lambda _user_id: True)
    assert client.get("/api/jobs/job-1/findings/status").status_code == 423
