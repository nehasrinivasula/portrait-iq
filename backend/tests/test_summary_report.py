"""
The diagnostic summary report (app/reporting/summary.py) and its endpoint,
on a small synthetic run: a job, a handful of events, the Signals the
analysis engine writes and a tool results file. Values are made up.
"""
import json
from datetime import datetime, timedelta

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.analysis.engine import run_analysis
from app.auth import User, get_active_user, get_current_user
from app.db import Base, get_db
from app.main import app
from app.models import AuditLogEntry, IngestedFile, Job
from app.models import NormalizedEvent as EventRow
from app.orchestrator.jobs import job_output_dir
from app.reporting.summary import DRAFT_NOTE, build_summary, message_shape, render_markdown, summary_to_json

T0 = datetime(2026, 10, 1, 2, 0, 0)
PING = "ping scheduler: no reply from hub"


def add(db, job_id, seconds, severity, message, device="hub-1", process="svc"):
    db.add(EventRow(job_id=job_id, timestamp=T0 + timedelta(seconds=seconds), source="portrait",
                    severity=severity, message=message, device_id=device, process=process,
                    category="General", source_file="a.csv", source_line=seconds))


def seed_run(db, job_id="job-1", status="done", results=None, rows_read=None):
    db.add(Job(id=job_id, mode="live", status=status, tools=json.dumps(["netshoot", "rti"]),
               requested_by="tester", created_at=T0, finished_at=T0 + timedelta(minutes=5)))
    add(db, job_id, 10, "ERROR", PING, device="hub-2", process="scheduler")
    add(db, job_id, 70, "ERROR", PING, device="hub-1", process="scheduler")
    add(db, job_id, 20, "ERROR", "Request timeout after 3021 ms for 1f0e2d3c-aaaa-bbbb-cccc-123456789abc")
    add(db, job_id, 30, "ERROR", "Request timeout after 15 ms for 9a8b7c6d-aaaa-bbbb-cccc-123456789abc", device="hub-3")
    add(db, job_id, 40, "WARN", "lookup failed patientName=John Smith", process="viewer")
    add(db, job_id, 50, "INFO", "all good")
    n = 6
    db.add(IngestedFile(source_file="a.csv", job_id=job_id, parser="portrait_csv",
                        rows_read=rows_read or n, events_written=n, rows_rejected=(rows_read or n) - n))
    db.commit()
    run_analysis(db, job_id)
    path = job_output_dir(job_id) / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results if results is not None else [
        {"tool": "netshoot", "artifact_paths": [], "summary": "Network checks passed", "warnings": [], "checks": []},
    ]))


def test_message_shape_groups_lines_that_differ_only_in_values():
    a = message_shape("Request timeout after 3021 ms for 1f0e2d3c-aaaa-bbbb-cccc-123456789abc")
    b = message_shape("Request timeout after 15 ms for 9a8b7c6d-aaaa-bbbb-cccc-123456789abc")
    assert a == b == "Request timeout after <n> ms for <id>"
    assert message_shape("open 'C:/x/y.dcm' failed") == "open '…' failed"


def test_findings_get_first_last_seen_devices_and_components(db):
    seed_run(db)
    s = build_summary(db, "job-1")
    ping = next(f for f in s.findings if f.rule_id == "ping_timeout")
    assert ping.spread.count == 2
    assert ping.spread.first_seen == T0 + timedelta(seconds=10)
    assert ping.spread.last_seen == T0 + timedelta(seconds=70)
    assert set(ping.spread.devices) == {"hub-1", "hub-2"}
    assert set(ping.spread.processes) == {"scheduler"}
    assert s.events == 6


def test_other_errors_exclude_rule_matches_and_group_by_shape(db):
    seed_run(db)
    s = build_summary(db, "job-1")
    messages = [g.message for g in s.other_errors]
    assert not any("ping scheduler" in m for m in messages)
    timeout = next(g for g in s.other_errors if g.message.startswith("Request timeout"))
    assert timeout.spread.count == 2 and set(timeout.spread.devices) == {"hub-1", "hub-3"}
    assert s.other_errors[0].severity == "ERROR"          # errors before warnings
    assert s.other_error_total == 3


def test_masking_hides_personal_data_in_message_shapes(db):
    seed_run(db)
    masked = render_markdown(build_summary(db, "job-1"))
    assert "John Smith" not in masked and "[REDACTED:" in masked
    assert "John Smith" in render_markdown(build_summary(db, "job-1", mask=False))


def test_masking_covers_device_and_component_names_and_long_messages(db):
    seed_run(db)
    add(db, "job-1", 60, "WARN", "x" * 150 + " patientName=Jane Roe", device="patientName=Mary Major",
        process="patientName=Ann Lee")
    db.commit()
    md = render_markdown(build_summary(db, "job-1"))
    for name in ("Jane Roe", "Mary Major", "Ann Lee"):
        assert name not in md
    s = build_summary(db, "job-1", mask=False)
    assert any("Mary Major" in d for g in s.other_errors for d in g.spread.devices)


def test_masking_keeps_equipment_addresses_readable(db):
    seed_run(db)
    add(db, "job-1", 80, "ERROR", "link down on 10.0.0.7", device="44-4b-5d-01-04-19")
    db.commit()
    md = render_markdown(build_summary(db, "job-1"))
    assert "44-4b-5d-01-04-19" in md and "10.0.0.7" not in md      # the IP is blanked as <n>, not redacted
    assert "REDACTED:mac" not in md and "REDACTED:ip" not in md


def test_failed_diagnostic_and_incomplete_logs_become_next_steps(db):
    seed_run(db, results=[{"tool": "rti", "error": "pod did not start"}], rows_read=9)
    s = build_summary(db, "job-1")
    assert s.logs_complete is False and s.completeness_issues
    assert s.next_steps[0].startswith("Re-run the rti diagnostic (pod did not start)")
    assert any("data completeness" in step for step in s.next_steps)
    assert "problems needing attention" in s.verdict
    assert "FAILED, did not finish: pod did not start" in render_markdown(s)


def test_unconfirmed_actions_are_labelled_draft_and_not_repeated(db):
    seed_run(db)
    s = build_summary(db, "job-1")
    finding_steps = [step for step in s.next_steps if "[" in step]
    assert finding_steps and all(step.endswith("(draft)") for step in finding_steps)
    assert DRAFT_NOTE in render_markdown(s)
    assert len(s.next_steps) == len(set(s.next_steps))


def test_markdown_has_every_section_and_json_matches(db):
    seed_run(db)
    s = build_summary(db, "job-1")
    md = render_markdown(s)
    for heading in ("# Diagnostic summary", "## Suggested next steps", "## Key errors found by the analysis",
                    "## Other errors and warnings", "## Diagnostics", "## Data completeness"):
        assert heading in md
    assert "1f0e2d3c" not in md                         # raw ids are blanked
    assert "2026-10-01 02:00:10 UTC" in md
    body = summary_to_json(s)
    assert body["job_id"] == "job-1" and body["events"] == 6
    assert body["findings"][0]["first_seen"].endswith("+00:00")
    json.dumps(body)                                      # serialisable as is


def test_unfinished_and_empty_runs_say_so(db):
    db.add(Job(id="job-2", mode="live", status="running", created_at=T0))
    db.commit()
    s = build_summary(db, "job-2")
    assert s.events == 0 and not s.findings
    assert s.logs_complete is False                       # completeness says the run is still going
    assert "has not finished" in s.verdict
    assert "No known problem patterns" in render_markdown(s)
    assert build_summary(db, "nope") is None


@pytest.fixture
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    user = User(id="tester", name="Tester")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_active_user] = lambda: user
    seed = Session()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def test_endpoint_markdown_json_404_and_audit(api):
    client, db = api
    seed_run(db)
    r = client.get("/api/jobs/job-1/summary")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "# Diagnostic summary" in r.text and "John Smith" not in r.text
    assert client.get("/api/jobs/job-1/summary", params={"format": "json"}).json()["job_id"] == "job-1"
    assert client.get("/api/jobs/missing/summary").status_code == 404
    assert client.get("/api/jobs/job-1/summary", params={"format": "pdf"}).status_code == 422
    audits = db.scalars(select(AuditLogEntry).where(AuditLogEntry.action == "summary_view")).all()
    assert len(audits) == 2
    assert all(a.detail.startswith("job=job-1") and "Smith" not in a.detail for a in audits)


def test_endpoint_requires_sign_in():
    app.dependency_overrides.clear()
    assert TestClient(app).get("/api/jobs/job-1/summary").status_code == 401
