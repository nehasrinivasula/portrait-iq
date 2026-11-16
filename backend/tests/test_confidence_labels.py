"""
Confidence labeling: every finding says, from the real lookup outcome, whether
it was confirmed against a manual, searched with no match, or not checked, and
nothing is ever shown as confirmed without a manual reference.

The mapping files below are made up (no real manual content).
"""
import json
import zipfile
from datetime import datetime, timedelta

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.knowledge.lookup as lookup_module
from app import db as dbmodule
from app.analysis.engine import run_analysis
from app.analysis.findings import findings_for
from app.auth import User, get_current_user
from app.config import settings
from app.db import Base, get_db
from app.knowledge import mapping as mapping_module
from app.knowledge.lookup import KnowledgeMatch, safe_lookup, status_label, stored_outcome
from app.main import app
from app.models import Job, NormalizedEvent
from app.packaging import service as packaging
from app.reporting.summary import build_summary, render_markdown, summary_to_json

T0 = datetime(2024, 6, 2, 10, 0, 0)
PING = "Request/Response Timeout observed during the ping scheduler execution"

CONFIRMED_PING = {"status": "confirmed", "document": "Example Service Manual", "section": "9.9 Pings",
                  "page": 12, "actions": ["Example step from the manual"], "reviewed_by": "Pat Example"}
NO_MATCH_DDS = {"status": "no_match", "searched": ["Example Service Manual"]}


@pytest.fixture(autouse=True)
def _no_mapping(monkeypatch):
    """Each test starts with no mapping configured and nothing cached."""
    mapping_module._cache.clear()
    monkeypatch.setattr(settings, "manual_mapping_file", None)


def use_mapping(tmp_path, monkeypatch, rules, version="v1", name="mapping.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"version": version, "rules": rules}))
    mapping_module._cache.clear()
    monkeypatch.setattr(settings, "manual_mapping_file", str(path))
    return path


def seed(db, job_id="job-1"):
    db.add(Job(id=job_id, mode="live", status="done"))
    for i in range(3):
        db.add(NormalizedEvent(timestamp=T0 + timedelta(minutes=i), source="portrait", severity="ERROR",
                               message=PING, device_id="hub-a", job_id=job_id, source_file="a.csv", source_line=i))
    db.add(NormalizedEvent(timestamp=T0, source="portrait", severity="WARN", device_id="hub-b", job_id=job_id,
                           message="!DDS DISCSimpleParticipantDiscoveryPluginReaderListener_onSampleLost: ff01",
                           source_file="a.csv", source_line=9))
    db.add(NormalizedEvent(timestamp=T0, source="portrait", severity="WARN", device_id="hub-c", job_id=job_id,
                           message="proc: Bad value for 'hidepid'", source_file="a.csv", source_line=10))
    db.commit()


def by_rule(signals):
    return {s.rule_id: s for s in signals}


# --- the lookup ------------------------------------------------------------------------

def test_no_mapping_means_not_checked_with_a_reason():
    match = safe_lookup("ping_timeout")
    assert match.status == "not_checked" and match.manual_ref is None and match.actions == []
    assert match.note == "No manual mapping is configured."
    assert status_label(match.status) == "Not checked against a manual"


def test_confirmed_entry_gives_a_readable_reference_and_the_manuals_steps(tmp_path, monkeypatch):
    use_mapping(tmp_path, monkeypatch, {"ping_timeout": CONFIRMED_PING})
    match = safe_lookup("ping_timeout")
    assert match.status == "confirmed" and match.version == "v1"
    assert match.manual_ref == "Example Service Manual, section 9.9 Pings, p. 12"
    assert match.actions == ["Example step from the manual"]
    assert "Pat" not in json.dumps(vars(match))            # reviewer names stay in the file


@pytest.mark.parametrize("entry", [
    {"status": "confirmed", "document": "Example Service Manual"},          # no section
    {"status": "confirmed", "section": "9.9"},                              # no document
    {"status": "confirmed", "document": " ", "section": " "},
    {"status": "no_match"},                                                 # no proof of a search
    {"status": "no_match", "searched": []},
    {"status": "maybe"},
    "confirmed",
])
def test_invalid_entries_are_not_checked(tmp_path, monkeypatch, entry):
    use_mapping(tmp_path, monkeypatch, {"ping_timeout": entry})
    match = safe_lookup("ping_timeout")
    assert match.status == "not_checked" and match.manual_ref is None


def test_no_match_needs_a_search_and_carries_no_reference(tmp_path, monkeypatch):
    use_mapping(tmp_path, monkeypatch, {"dds_security": NO_MATCH_DDS})
    match = safe_lookup("dds_security")
    assert match.status == "no_match" and match.manual_ref is None and match.actions == []
    assert status_label(match.status) == "No match found"


def test_a_rule_not_in_the_mapping_is_not_reviewed(tmp_path, monkeypatch):
    use_mapping(tmp_path, monkeypatch, {"ping_timeout": CONFIRMED_PING})
    match = safe_lookup("hidepid")
    assert match.status == "not_checked" and match.note == "Not reviewed against the manuals yet."


@pytest.mark.parametrize("content", ["{not json", json.dumps([1, 2]), json.dumps({"rules": "x"}), None])
def test_an_unreadable_or_missing_mapping_is_not_checked(tmp_path, monkeypatch, content):
    path = tmp_path / "mapping.json"
    if content is not None:
        path.write_text(content)
    monkeypatch.setattr(settings, "manual_mapping_file", str(path))
    match = safe_lookup("ping_timeout")
    assert match.status == "not_checked" and match.note == "The manual mapping could not be read."


def test_a_lookup_that_fails_is_not_checked_never_confirmed(monkeypatch):
    def broken(rule_id):
        raise RuntimeError("index broken")
    monkeypatch.setattr(lookup_module, "lookup_signal", broken)
    match = safe_lookup("ping_timeout")
    assert match.status == "not_checked" and match.note == "Lookup failed (RuntimeError)."


def test_confirmed_without_a_reference_is_downgraded_by_the_guard(monkeypatch):
    monkeypatch.setattr(lookup_module, "lookup_signal",
                        lambda rule_id: KnowledgeMatch("confirmed", manual_ref="  ", actions=["guess"]))
    match = safe_lookup("ping_timeout")
    assert match.status == "not_checked" and match.manual_ref is None and match.actions == []
    monkeypatch.setattr(lookup_module, "lookup_signal",
                        lambda rule_id: KnowledgeMatch("no_match", manual_ref="Doc, section 1", actions=["x"]))
    match = safe_lookup("ping_timeout")
    assert match.status == "no_match" and match.manual_ref is None and match.actions == []


def test_saved_rows_are_shown_honestly():
    assert stored_outcome("unconfirmed", None) == ("not_checked", None)           # the old value
    assert stored_outcome("confirmed", None) == ("not_checked", None)             # damaged row
    assert stored_outcome("no_match", "Doc, section 1") == ("no_match", None)
    assert stored_outcome("confirmed", "Doc, section 1") == ("confirmed", "Doc, section 1")
    assert stored_outcome("weird", None) == ("not_checked", None)


# --- the engine, the API, the summary and the export ------------------------------------

def test_engine_stores_the_outcome_and_uses_manual_steps_only_when_confirmed(db, tmp_path, monkeypatch):
    use_mapping(tmp_path, monkeypatch, {"ping_timeout": CONFIRMED_PING, "dds_security": NO_MATCH_DDS})
    seed(db)
    signals = by_rule(run_analysis(db, "job-1"))
    ping, dds, hide = signals["ping_timeout"], signals["dds_security"], signals["hidepid"]
    assert (ping.status, ping.manual_ref, ping.knowledge_version) == (
        "confirmed", "Example Service Manual, section 9.9 Pings, p. 12", "v1")
    assert json.loads(ping.actions_json) == ["Example step from the manual"] and ping.checked_at is not None
    assert (dds.status, dds.manual_ref) == ("no_match", None)
    assert json.loads(dds.actions_json)[0].startswith("Check clock sync")          # the rule's drafts
    assert (hide.status, hide.lookup_note) == ("not_checked", "Not reviewed against the manuals yet.")


def test_analysis_still_finishes_when_the_lookup_breaks(db, monkeypatch):
    monkeypatch.setattr(lookup_module, "lookup_signal", lambda rule_id: 1 / 0)
    seed(db)
    signals = run_analysis(db, "job-1")
    assert signals and all(s.status == "not_checked" for s in signals)
    assert all(s.lookup_note == "Lookup failed (ZeroDivisionError)." for s in signals)


def test_findings_api_and_export_carry_the_labels(db, tmp_path, monkeypatch):
    use_mapping(tmp_path, monkeypatch, {"ping_timeout": CONFIRMED_PING, "dds_security": NO_MATCH_DDS})
    monkeypatch.setattr(settings, "job_output_dir", str(tmp_path / "jobs"))
    seed(db)
    run_analysis(db, "job-1")
    out = {f.rule_id: f for f in findings_for(db, "job-1")}
    assert out["ping_timeout"].status_label == "Confirmed against manual"
    assert out["ping_timeout"].manual_ref.startswith("Example Service Manual") and out["ping_timeout"].lookup_note is None
    assert out["dds_security"].status_label == "No match found" and out["dds_security"].manual_ref is None
    assert out["hidepid"].status_label == "Not checked against a manual"
    assert out["hidepid"].lookup_note == "Not reviewed against the manuals yet."

    packaging.start(db, "job-1", masked=True, requested_by="tester")
    status = packaging.build(db, "job-1")
    with zipfile.ZipFile(packaging.zip_path(status)) as z:
        exported = {f["rule_id"]: f for f in json.loads(z.read("findings/findings.json"))}
    assert exported["ping_timeout"]["status_label"] == "Confirmed against manual"
    assert exported["dds_security"]["status"] == "no_match"
    assert "Pat" not in json.dumps(exported)


def test_summary_labels_findings_and_marks_drafts(db, tmp_path, monkeypatch):
    use_mapping(tmp_path, monkeypatch, {"ping_timeout": CONFIRMED_PING, "dds_security": NO_MATCH_DDS})
    monkeypatch.setattr(settings, "job_output_dir", str(tmp_path / "jobs"))
    seed(db)
    run_analysis(db, "job-1")
    s = build_summary(db, "job-1")
    assert s.analysis == "current"
    md = render_markdown(s)
    assert "Manual match: Confirmed against manual: Example Service Manual, section 9.9 Pings, p. 12" in md
    assert "Manual match: No match found (The manuals were searched; none covers this.)" in md
    assert "Manual match: Not checked against a manual (Not reviewed against the manuals yet.)" in md
    assert any(step.startswith("Example step from the manual") and not step.endswith("(draft)") for step in s.next_steps)
    finding_steps = [step for step in s.next_steps if " [" in step]      # "<action> [<finding title>]"
    assert all(step.endswith("(draft)") for step in finding_steps if "Example step" not in step)
    body = summary_to_json(s)
    assert {f["rule_id"]: f["status_label"] for f in body["findings"]}["dds_security"] == "No match found"


def test_editing_the_mapping_marks_saved_findings_out_of_date(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "job_output_dir", str(tmp_path / "jobs"))
    use_mapping(tmp_path, monkeypatch, {})
    seed(db)
    run_analysis(db, "job-1")
    assert build_summary(db, "job-1").analysis == "current"
    use_mapping(tmp_path, monkeypatch, {"ping_timeout": CONFIRMED_PING}, version="v2", name="mapping2.json")
    s = build_summary(db, "job-1")
    assert s.analysis == "out_of_date" and any("manual mapping" in n for n in s.notes)
    run_analysis(db, "job-1")                                       # re-run picks up the new mapping
    s = build_summary(db, "job-1")
    assert s.analysis == "current"
    assert next(f for f in s.findings if f.rule_id == "ping_timeout").status == "confirmed"


def test_findings_route_reports_labels(monkeypatch, tmp_path):
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
    try:
        seed(Session())
        use_mapping(tmp_path, monkeypatch, {"ping_timeout": CONFIRMED_PING})
        body = TestClient(app).post("/api/jobs/job-1/findings/run").json()
        labels = {f["rule_id"]: (f["status"], f["status_label"]) for f in body}
        assert labels["ping_timeout"] == ("confirmed", "Confirmed against manual")
        assert labels["hidepid"] == ("not_checked", "Not checked against a manual")
        assert all(f["checked_at"] and f["knowledge_version"] == "v1" for f in body)
    finally:
        app.dependency_overrides.clear()


# --- upgrading an existing database -----------------------------------------------------

def test_existing_database_is_upgraded_and_old_statuses_rewritten(tmp_path, monkeypatch):
    old = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with old.begin() as c:
        c.execute(text("""CREATE TABLE signals (id VARCHAR NOT NULL PRIMARY KEY, job_id VARCHAR NOT NULL,
            rule_id VARCHAR NOT NULL, title VARCHAR NOT NULL, severity VARCHAR NOT NULL, count INTEGER,
            status VARCHAR, manual_ref VARCHAR, actions_json TEXT)"""))
        c.execute(text("INSERT INTO signals (id, job_id, rule_id, title, severity, count, status) "
                       "VALUES ('s1', 'j', 'hidepid', 'old', 'warning', 3, 'unconfirmed')"))
    monkeypatch.setattr(dbmodule, "engine", old)
    dbmodule.init_db()
    dbmodule.init_db()                                              # running twice is harmless
    columns = {c["name"] for c in inspect(old).get_columns("signals")}
    assert {"checked_at", "knowledge_version", "lookup_note"} <= columns
    with old.connect() as c:
        assert c.execute(text("SELECT title, status, count FROM signals WHERE id = 's1'")).one() == (
            "old", "not_checked", 3)
    old.dispose()
