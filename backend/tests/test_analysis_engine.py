"""run_analysis over a real (throwaway) event store: findings with evidence, scoped to one collection,
replaced on a re-run, with PHI/PII masked when shown; plus the coverage report and the in-place upgrade."""
import json
from datetime import datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, inspect, text

import app.analysis.engine as engine_module
from app import db as dbmodule
from app.analysis.coverage import coverage_report
from app.analysis.engine import run_analysis
from app.analysis.findings import findings_for
from app.models import Job, NormalizedEvent, Signal, SignalEvidence

T0 = datetime(2024, 6, 2, 10, 0, 0)


def add(db, i, message, severity="ERROR", device="hub-a", job_id="job-1"):
    db.add(NormalizedEvent(
        timestamp=T0 + timedelta(minutes=i), source="portrait", severity=severity, message=message,
        device_id=device, category="General", process="svc", job_id=job_id, source_file="a.csv", source_line=i,
    ))


@pytest.fixture
def seeded(db):
    db.add_all([Job(id="job-1", mode="live", status="done"), Job(id="job-2", mode="live", status="done")])
    for i in range(1, 31):
        add(db, i, "Request/Response Timeout observed during the ping scheduler execution",
            device="hub-a" if i % 3 else "hub-b")
    add(db, 40, "!DDS DISCSimpleParticipantDiscoveryPluginReaderListener_onSampleLost: ff0101c7", "WARN", "hub-b")
    add(db, 41, "Healthcheck succeeded in 12 ms", "WARN")
    add(db, 42, "axone_logger started", "INFO")
    add(db, 43, "Compressor stalled", "ERROR")                     # an error no rule knows
    add(db, 1, "ping scheduler timed out", job_id="job-2")          # another collection
    db.commit()
    return db


def by_rule(signals):
    return {s.rule_id: s for s in signals}


def test_each_known_pattern_becomes_a_finding_with_its_evidence(seeded):
    signals = by_rule(run_analysis(seeded, "job-1"))
    assert set(signals) == {"ping_timeout", "dds_security", "healthcheck_noise"}
    ping = signals["ping_timeout"]
    assert ping.count == 30 and ping.title == "Ping scheduler times out (30x)" and ping.severity == "error"
    assert ping.first_seen == T0 + timedelta(minutes=1) and ping.last_seen == T0 + timedelta(minutes=30)
    assert json.loads(ping.devices_json) == {"total": 2, "top": [["hub-a", 20], ["hub-b", 10]]}
    assert ping.status == "unconfirmed" and json.loads(ping.actions_json)


def test_only_the_named_collection_is_analysed(seeded):
    run_analysis(seeded, "job-1")
    assert seeded.query(Signal).filter(Signal.job_id == "job-2").count() == 0
    assert by_rule(run_analysis(seeded, "job-2"))["ping_timeout"].count == 1


def test_running_again_replaces_the_findings(seeded):
    run_analysis(seeded, "job-1")
    run_analysis(seeded, "job-1")
    assert seeded.query(Signal).filter(Signal.job_id == "job-1").count() == 3
    ids = {s.id for s in seeded.query(Signal).all()}
    assert {e.signal_id for e in seeded.query(SignalEvidence).all()} <= ids          # no orphaned evidence
    add(seeded, 50, "ping scheduler timed out")
    seeded.commit()
    assert by_rule(run_analysis(seeded, "job-1"))["ping_timeout"].count == 31


def test_a_collection_with_nothing_to_find_has_no_findings_and_clears_old_ones(seeded):
    run_analysis(seeded, "job-1")
    seeded.query(NormalizedEvent).filter(NormalizedEvent.job_id == "job-1").delete()
    seeded.commit()
    assert run_analysis(seeded, "job-1") == []
    assert findings_for(seeded, "job-1") == []


def test_events_are_read_in_batches_without_missing_any(seeded, monkeypatch):
    monkeypatch.setattr(engine_module, "BATCH", 7)
    assert by_rule(run_analysis(seeded, "job-1"))["ping_timeout"].count == 30


def test_findings_carry_the_earliest_and_latest_lines_as_evidence(seeded):
    run_analysis(seeded, "job-1")
    ping = by_rule(findings_for(seeded, "job-1"))["ping_timeout"]
    assert len(ping.evidence) == 10
    stamps = [e.timestamp for e in ping.evidence]
    assert stamps == sorted(stamps)
    assert ping.evidence[0].timestamp.minute == 1 and ping.evidence[-1].timestamp.minute == 30
    assert ping.evidence[0].source_file == "a.csv" and ping.evidence[0].source_line == 1
    assert "ping scheduler" in ping.evidence[0].message
    assert ping.devices_total == 2 and [(d.device_id, d.count) for d in ping.devices] == [("hub-a", 20), ("hub-b", 10)]
    assert ping.first_seen.tzinfo is not None


def test_evidence_is_masked_for_phi(db):
    db.add(Job(id="job-1", mode="live", status="done"))
    add(db, 1, "ping scheduler timed out for patientName=John Smith mrn=123456")
    db.commit()
    run_analysis(db, "job-1")
    text_out = by_rule(findings_for(db, "job-1"))["ping_timeout"].evidence[0].message
    assert "John Smith" not in text_out and "123456" not in text_out
    assert "[REDACTED:person_name]" in text_out and "ping scheduler timed out" in text_out


def test_evidence_for_a_removed_event_is_skipped_not_invented(seeded):
    run_analysis(seeded, "job-1")
    first = seeded.query(SignalEvidence).order_by(SignalEvidence.id).first()
    seeded.query(NormalizedEvent).filter(NormalizedEvent.id == first.event_id).delete()
    seeded.commit()
    signal = seeded.get(Signal, first.signal_id)
    shown = [s for s in findings_for(seeded, "job-1") if s.id == signal.id][0]
    assert shown.count == signal.count and first.event_id not in [e.event_id for e in shown.evidence]


def test_coverage_lists_the_errors_and_warnings_no_rule_explains(seeded):
    report = coverage_report(seeded, "job-1")
    assert report["events"] == 34 and report["problem_events"] == 33
    assert report["problem_unmatched"] == 1 and report["problem_matched"] == 32
    assert report["per_rule"]["ping_timeout"] == 30
    assert [(u["severity"], u["count"], u["example"]) for u in report["unmatched"]] == [("ERROR", 1, "Compressor stalled")]


def test_coverage_groups_repeats_of_one_shape_and_masks_examples(db):
    db.add(Job(id="job-1", mode="live", status="done"))
    for i in range(1, 6):
        add(db, i, f"Valve {i} stuck for patientName=Jane Doe after {i * 100} ms")
    db.commit()
    report = coverage_report(db, "job-1")
    assert len(report["unmatched"]) == 1 and report["unmatched"][0]["count"] == 5
    assert "Jane" not in report["unmatched"][0]["example"] and "Jane" not in report["unmatched"][0]["shape"]


def test_init_db_adds_the_evidence_columns_to_an_older_signals_table(tmp_path, monkeypatch):
    old = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with old.begin() as c:
        c.execute(text("""CREATE TABLE signals (id VARCHAR NOT NULL PRIMARY KEY, job_id VARCHAR NOT NULL,
            rule_id VARCHAR NOT NULL, title VARCHAR NOT NULL, severity VARCHAR NOT NULL, count INTEGER,
            status VARCHAR, manual_ref VARCHAR, actions_json TEXT)"""))
        c.execute(text("INSERT INTO signals (id, job_id, rule_id, title, severity, count) "
                       "VALUES ('s1', 'j', 'hidepid', 'old', 'warning', 3)"))
    monkeypatch.setattr(dbmodule, "engine", old)
    dbmodule.init_db()
    assert {"first_seen", "last_seen", "devices_json"} <= {c["name"] for c in inspect(old).get_columns("signals")}
    assert inspect(old).has_table("signal_evidence")
    with old.connect() as c:
        assert c.execute(text("SELECT title FROM signals WHERE id = 's1'")).scalar_one() == "old"
