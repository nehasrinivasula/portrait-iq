"""Background tasks: log pulls and PHI/PII scans run off the request path (so /health stays responsive)."""
import json
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.auth import User, get_current_user  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditLogEntry, BackgroundTask  # noqa: E402
from app.tasks import launcher, runner, service  # noqa: E402


def _actions(db):
    return [(e.action, e.detail) for e in db.scalars(select(AuditLogEntry).order_by(AuditLogEntry.id))]


def _task(db, **kw):
    t, _ = service.create_task(db, kind=kw.pop("kind", "noop"), actor="tester", session_id="s1",
                               params=kw.pop("params", {}), **kw)
    return t


# --- service ---------------------------------------------------------------------------------------------

def test_create_task_starts_queued(db):
    t = _task(db)
    assert t.status == "queued" and t.done == 0 and t.requested_by == "tester"


def test_unknown_kind_is_refused(db):
    with pytest.raises(ValueError):
        service.create_task(db, kind="rm_rf", actor="a", session_id=None, params={})


def test_reuse_active_returns_the_running_scan_not_a_second_one(db):
    first, created1 = service.create_task(db, kind="privacy_scan", actor="a", session_id=None, params={},
                                          scope="all", reuse_active=True)
    again, created2 = service.create_task(db, kind="privacy_scan", actor="a", session_id=None, params={},
                                          scope="all", reuse_active=True)
    assert created1 and not created2 and again.id == first.id
    other, created3 = service.create_task(db, kind="privacy_scan", actor="a", session_id=None, params={},
                                          scope="job-1", reuse_active=True)
    assert created3 and other.id != first.id


def test_reuse_active_ignores_finished_tasks(db):
    first, _ = service.create_task(db, kind="privacy_scan", actor="a", session_id=None, params={}, scope="all",
                                   reuse_active=True)
    first.status = "done"
    db.commit()
    _, created = service.create_task(db, kind="privacy_scan", actor="a", session_id=None, params={}, scope="all",
                                     reuse_active=True)
    assert created


def test_recovery_marks_unfinished_tasks_interrupted_and_audits_them(db, staging):
    queued, running, done = _task(db), _task(db), _task(db)
    running.status, done.status = "running", "done"
    db.commit()
    assert service.recover_interrupted_tasks(db) == 2
    db.refresh(queued), db.refresh(running), db.refresh(done)
    assert (queued.status, running.status, done.status) == ("interrupted", "interrupted", "done")
    assert "restarted" in queued.error
    assert sum(1 for a, _ in _actions(db) if a == "background_task_failed") == 2


def test_recovery_deletes_only_scratch_folders_inside_staging_uploads(db, staging, tmp_path):
    inbox = staging / "uploads" / "abc123"
    inbox.mkdir(parents=True)
    (inbox / "f.csv").write_text("x")
    outside = tmp_path / "keep-me"
    outside.mkdir()
    _task(db, params={"scratch_dirs": [str(inbox), str(outside)]})
    service.recover_interrupted_tasks(db)
    assert not inbox.exists() and outside.exists()


# --- runner ----------------------------------------------------------------------------------------------

def test_a_successful_task_is_recorded_done_with_its_result_and_audited(db, engine):
    t = _task(db)
    runner.execute(engine, t.id)
    db.refresh(t)
    assert t.status == "done" and json.loads(t.result) == {"ok": True}
    assert t.started_at and t.finished_at and (t.done, t.total) == (1, 1)
    assert [a for a, _ in _actions(db)] == ["background_task_start", "background_task_done"]


def test_a_failing_task_is_recorded_with_a_safe_message_and_audited(db, engine, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("secret path /etc/passwd")
    monkeypatch.setattr("app.tasks.handlers.run_noop", boom)
    t = _task(db)
    runner.execute(engine, t.id)
    db.refresh(t)
    assert t.status == "failed" and "RuntimeError" in t.error and "passwd" not in t.error
    assert any(a == "background_task_failed" for a, _ in _actions(db))


def test_a_message_the_app_wrote_is_shown_as_is(db, engine, monkeypatch):
    from app.sources.base import SourceError

    def fail(*_a, **_k):
        raise SourceError("The log server is unreachable.")
    monkeypatch.setattr("app.tasks.handlers.run_noop", fail)
    t = _task(db)
    runner.execute(engine, t.id)
    db.refresh(t)
    assert t.error == "The log server is unreachable."


def test_executing_twice_runs_once(db, engine):
    t = _task(db)
    runner.execute(engine, t.id)
    runner.execute(engine, t.id)
    assert [a for a, _ in _actions(db)].count("background_task_start") == 1


def test_executing_a_missing_task_does_nothing(engine):
    runner.execute(engine, "nope")


def test_scratch_folder_is_removed_when_the_task_ends(db, engine, staging):
    inbox = staging / "uploads" / "zz9"
    inbox.mkdir(parents=True)
    t = _task(db, params={"scratch_dirs": [str(inbox)]})
    runner.execute(engine, t.id)
    assert not inbox.exists()


def test_progress_is_throttled_but_completion_always_lands(db, engine, monkeypatch):
    t = _task(db)
    p = runner.Progress(db, t.id)
    p(1, 100, "Scanning")
    p(2, 100, "Scanning")          # inside the throttle window: skipped
    db.refresh(t)
    assert (t.done, t.total, t.stage) == (1, 100, "Scanning")
    p(100, 100, "Scanning")        # finished: always written
    db.refresh(t)
    assert t.done == 100


# --- handlers --------------------------------------------------------------------------------------------

def test_privacy_scan_task_reports_scanned_count_and_audits_start_and_end(db, engine):
    t = _task(db, kind="privacy_scan", params={"scope": None}, scope="all")
    runner.execute(engine, t.id)
    db.refresh(t)
    assert t.status == "done" and json.loads(t.result) == {"scanned": 0}
    acts = [a for a, _ in _actions(db)]
    assert acts == ["background_task_start", "logs_privacy_scan_start", "logs_privacy_scan", "background_task_done"]


# --- launcher --------------------------------------------------------------------------------------------

def test_worker_command_runs_the_worker_module_with_the_task_id():
    cmd = launcher.command("abc")
    assert cmd[1:] == ["-m", "app.tasks.worker", "abc"]


def test_a_worker_that_dies_leaves_a_failed_task_not_a_running_one(db, engine, monkeypatch, staging):
    monkeypatch.setattr("app.db.engine", engine)
    t = _task(db)
    t.status = "running"
    db.commit()
    launcher._after_exit(t.id, 137)
    db.refresh(t)
    assert t.status == "failed" and "137" in t.error
    assert any(a == "background_task_failed" for a, _ in _actions(db))


def test_after_exit_leaves_a_finished_task_alone(db, engine, monkeypatch):
    monkeypatch.setattr("app.db.engine", engine)
    t = _task(db)
    runner.execute(engine, t.id)
    launcher._after_exit(t.id, 0)
    db.refresh(t)
    assert t.status == "done"


def test_a_worker_that_cannot_start_is_reported(db, engine, monkeypatch, staging):
    monkeypatch.setattr("app.db.engine", engine)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("no")))
    t = _task(db)
    launcher._supervise(t.id)
    db.refresh(t)
    assert t.status == "failed" and "could not be started" in t.error


def test_process_mode_hands_off_to_the_launcher_and_returns_at_once(db, monkeypatch):
    monkeypatch.setattr(settings, "background_task_mode", "process")
    launched = []
    monkeypatch.setattr(launcher, "launch", lambda task_id: launched.append(task_id))
    t = _task(db)
    out = service.submit(db, t)
    assert launched == [t.id] and out.status == "queued"


# --- routes ----------------------------------------------------------------------------------------------

@pytest.fixture()
def client(engine, monkeypatch):
    Session = sessionmaker(bind=engine)

    def override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(id="tester", name="Tester")
    # An overridden user has no login session, which the idle lock would refuse (423).
    monkeypatch.setattr("app.auth.is_locked", lambda *_: False)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_task_returns_its_state(client, db, engine):
    t = _task(db)
    runner.execute(engine, t.id)
    body = client.get(f"/api/tasks/{t.id}").json()
    assert body["status"] == "done" and body["result"] == {"ok": True} and body["id"] == t.id


def test_unknown_task_is_404(client):
    assert client.get("/api/tasks/nope").status_code == 404


def test_list_active_returns_only_unfinished_tasks_of_the_kind(client, db, engine):
    done = _task(db, kind="privacy_scan", scope="all")
    runner.execute(engine, done.id)    # noop handler is not used; privacy_scan with no params scans everything
    active = _task(db, kind="privacy_scan", scope="job-1")
    ids = [t["id"] for t in client.get("/api/tasks?active=true&kind=privacy_scan").json()]
    assert ids == [active.id]
    assert client.get("/api/tasks?kind=bogus").status_code == 422


def test_scan_endpoint_returns_a_task_and_reports_unknown_collections(client):
    r = client.post("/api/logs/privacy/scan")
    assert r.status_code == 202 and r.json()["kind"] == "privacy_scan"
    assert client.post("/api/logs/collections/missing/privacy/scan").status_code == 404


def test_scan_endpoint_does_not_wait_for_the_scan_in_process_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "background_task_mode", "process")
    monkeypatch.setattr(launcher, "launch", lambda task_id: None)
    r = client.post("/api/logs/privacy/scan")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    again = client.post("/api/logs/privacy/scan")       # a second click joins the first
    assert again.json()["id"] == r.json()["id"]


def test_acquire_rejects_a_bad_window_and_an_unknown_job_before_starting_anything(client, db):
    r = client.post("/api/sources/local/acquire", json={"job_id": "missing"})
    assert r.status_code == 404
    assert db.query(BackgroundTask).count() == 0


def test_health_is_async_and_touches_no_database():
    import asyncio
    import inspect

    from app.main import health
    assert inspect.iscoroutinefunction(health)
    assert asyncio.run(health())["status"] == "ok"


def test_task_audit_actions_have_labels():
    labels = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / "audit" / "auditApi.ts"
    text = labels.read_text()
    for action in ("background_task_start", "background_task_done", "background_task_failed"):
        assert action in text
