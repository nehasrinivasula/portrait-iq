"""PHI/PII flags through /api/logs: flags on events, mask, filters, the report,
and that none of it leaks values into the audit log. Same setup as
test_log_routes.py: real routes, in-memory database, fixed signed-in user."""
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import User, get_current_user
from app.db import Base, get_db
from app.ingestion import pipeline
from app.main import app
from app.models import AuditLogEntry, Job
from app.models import NormalizedEvent as EventRow
from app.privacy.scanner import scan_columns

HEADER = (
    "timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,"
    "sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description"
)
NAME, SSN = "John Smith", "123-45-6789"


@pytest.fixture
def env(tmp_path):
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
    seed.commit()
    lines = [HEADER] + [
        f'"2024-06-02T04:00:{i:02d}.000+0000","0","{i}","INFO","host-a","u","unsetFriendlyName","svc[1:0]",":0","General","{m}"'
        for i, m in enumerate(["all quiet", f"patientName={NAME} admitted", f"billing ssn {SSN}"])
    ]
    path = tmp_path / "a.csv"
    path.write_text("\n".join(lines) + "\n")
    pipeline.ingest_file(path, seed, job_id="job-1")
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def items(client, **params):
    r = client.get("/api/logs/events", params={"order": "asc", **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_events_carry_their_flags_without_the_text(env):
    client, _ = env
    quiet, name, billing = items(client)
    assert quiet["privacy_scanned"] and quiet["sensitive"] == [] and not quiet["masked"]
    flag, = name["sensitive"]
    assert (flag["field"], flag["category"], flag["confidence"]) == ("message", "person_name", "high")
    assert name["message"][flag["start"]:flag["end"]] == NAME
    assert set(flag) == {"field", "category", "confidence", "start", "end"}
    assert billing["sensitive"][0]["category"] == "ssn"


def test_default_responses_are_not_masked(env):
    client, _ = env
    assert NAME in items(client)[1]["message"]


def test_mask_replaces_flagged_text_and_hides_positions(env):
    client, _ = env
    name, billing = items(client, mask=True)[1:]
    assert name["message"] == "patientName=[REDACTED:person_name] admitted" and name["masked"]
    assert SSN not in billing["message"] and "[REDACTED:ssn]" in billing["message"]
    assert name["sensitive"][0]["start"] is None and name["sensitive"][0]["end"] is None
    assert items(client, mask=True)[0]["message"] == "all quiet"


def test_mask_applies_to_the_unified_view_too(env):
    client, _ = env
    r = client.get("/api/logs/collections/job-1/events", params={"mask": True})
    text = " ".join(e["message"] for e in r.json()["items"])
    assert NAME not in text and SSN not in text


def test_search_filters_on_flags(env):
    client, _ = env
    # source_line counts the file's lines and line 1 is the header, so the three messages are lines 2-4.
    assert [e["source_line"] for e in items(client, flagged=True)] == [3, 4]
    assert [e["source_line"] for e in items(client, flagged=False)] == [2]
    assert [e["source_line"] for e in items(client, flag_category="ssn")] == [4]
    assert client.get("/api/logs/events", params={"flag_category": "nope"}).status_code == 400


def test_events_not_scanned_by_the_current_scanner_say_so(env):
    client, db = env
    db.add(EventRow(timestamp=db.scalars(select(EventRow.timestamp)).first(), source="portrait", severity="INFO",
                    message="legacy", job_id="job-1", source_file="old.csv", source_line=1))
    db.commit()
    legacy = [e for e in items(client) if e["source_file"] == "old.csv"][0]
    assert legacy["privacy_scanned"] is False


def test_privacy_report_for_a_collection(env):
    client, _ = env
    r = client.get("/api/logs/collections/job-1/privacy")
    assert r.status_code == 200
    body = r.json()
    assert (body["events"], body["flagged_events"], body["unscanned_events"], body["all_scanned"]) == (3, 2, 0, True)
    assert {c["value"]: c["count"] for c in body["by_category"]} == {"person_name": 1, "ssn": 1}
    assert body["files"] == [{"source_file": "a.csv", "events": 3, "flagged_events": 2, "unscanned_events": 0,
                              "network_identifier_events": 0}]
    assert body["network_identifier_events"] == 0 and body["network_by_category"] == []
    assert body["limits"]
    assert NAME not in r.text and SSN not in r.text


def test_privacy_report_404s_for_an_unknown_collection(env):
    client, _ = env
    assert client.get("/api/logs/collections/missing/privacy").status_code == 404
    assert client.get("/api/logs/collections/unassigned/privacy").status_code == 200


def test_scan_endpoint_judges_unscanned_events(env):
    client, db = env
    db.add(EventRow(timestamp=db.scalars(select(EventRow.timestamp)).first(), source="portrait", severity="INFO",
                    message=f"legacy {SSN}", job_id="job-1", source_file="old.csv", source_line=1))
    db.commit()
    assert client.get("/api/logs/collections/job-1/privacy").json()["all_scanned"] is False
    assert client.post("/api/logs/collections/job-1/privacy/scan").json() == {"scanned": 1}
    after = client.get("/api/logs/collections/job-1/privacy").json()
    assert after["all_scanned"] and after["flagged_events"] == 3
    assert client.post("/api/logs/collections/job-1/privacy/scan").json() == {"scanned": 0}
    assert client.post("/api/logs/collections/missing/privacy/scan").status_code == 404


def test_categories_endpoint_lists_every_kind(env):
    client, _ = env
    body = client.get("/api/logs/privacy/categories").json()
    kinds = {c["category"] for c in body}
    assert {"ssn", "email", "person_name", "hl7_patient_segment"} <= kinds
    assert all(c["description"] for c in body)


def test_audit_log_records_privacy_actions_without_values(env):
    client, db = env
    items(client, flagged=True, flag_category="ssn", mask=True)
    client.get("/api/logs/collections/job-1/privacy")
    client.post("/api/logs/collections/job-1/privacy/scan")
    entries = list(db.scalars(select(AuditLogEntry)))
    actions = {e.action for e in entries}
    assert {"logs_search", "logs_privacy_report", "logs_privacy_scan"} <= actions
    blob = " ".join(f"{e.action} {e.detail}" for e in entries)
    assert NAME not in blob and SSN not in blob and "ssn" not in blob.replace("logs_privacy", "")
    assert "flagged" in blob and "flag_category" in blob


def test_a_locked_session_cannot_read_privacy_reports(env, monkeypatch):
    client, _ = env
    import app.storage.routes as routes
    monkeypatch.setattr(routes, "is_locked", lambda _uid: True)
    assert client.get("/api/logs/collections/job-1/privacy").status_code == 423
    assert client.post("/api/logs/collections/job-1/privacy/scan").status_code == 423
    assert client.get("/api/logs/privacy/categories").status_code == 423


def test_events_list_network_identifiers_apart_and_mask_leaves_them(env):
    client, db = env
    db.add(EventRow(timestamp=db.scalars(select(EventRow.timestamp)).first(), source="portrait", severity="INFO",
                    message=f"{SSN} peer 10.0.0.5", device_id="00:1A:2B:3C:4D:5E", job_id="job-1",
                    source_file="net.csv", source_line=30, **scan_columns(
                        message=f"{SSN} peer 10.0.0.5", device_id="00:1A:2B:3C:4D:5E")))
    db.commit()
    plain = [e for e in items(client) if e["source_file"] == "net.csv"][0]
    assert [f["category"] for f in plain["sensitive"]] == ["ssn"]
    assert {f["category"] for f in plain["network_identifiers"]} == {"ip_address", "mac_address"}
    masked = [e for e in items(client, mask=True) if e["source_file"] == "net.csv"][0]
    assert "10.0.0.5" in masked["message"] and SSN not in masked["message"]
    assert masked["device_id"] == "00:1A:2B:3C:4D:5E"
    report = client.get("/api/logs/collections/job-1/privacy").json()
    assert report["network_identifier_events"] == 1
    assert {c["value"] for c in report["network_by_category"]} == {"ip_address", "mac_address"}
    assert {c["category"]: c["group"] for c in client.get("/api/logs/privacy/categories").json()}["mac_address"] == "network"


def _legacy(db, message, **extra):
    """An event stored before flagging existed: never scanned."""
    db.add(EventRow(timestamp=db.scalars(select(EventRow.timestamp)).first(), source="portrait", severity="INFO",
                    message=message, job_id="job-1", source_file="old.csv", source_line=1, **extra))
    db.commit()


def test_mask_judges_unscanned_events_on_the_spot_instead_of_returning_raw_text(env):
    client, db = env
    _legacy(db, f"legacy {SSN}")
    plain = [e for e in items(client) if e["source_file"] == "old.csv"][0]
    assert SSN in plain["message"] and plain["privacy_scanned"] is False
    masked = [e for e in items(client, mask=True) if e["source_file"] == "old.csv"][0]
    assert SSN not in masked["message"] and "[REDACTED:ssn]" in masked["message"]
    assert masked["masked"] is True and masked["privacy_scanned"] is False


def test_mask_withholds_text_when_the_stored_flags_cannot_be_read(env):
    client, db = env
    from app.privacy.scanner import SCANNER_VERSION
    _legacy(db, f"damaged {SSN}", device_id="host-x", privacy_scan_version=SCANNER_VERSION, sensitive_flags="not json")
    masked = [e for e in items(client, mask=True) if e["source_file"] == "old.csv"][0]
    assert masked["message"] == "[REDACTED:unreadable]" and masked["device_id"] == "[REDACTED:unreadable]"
    assert masked["process"] is None and masked["masked"] is True
    assert SSN not in client.get("/api/logs/events", params={"mask": True}).text


def test_mask_hides_network_identifier_positions_too(env):
    client, db = env
    _legacy(db, "peer 10.0.0.5", **scan_columns(message="peer 10.0.0.5"))
    plain = [e for e in items(client) if e["source_file"] == "old.csv"][0]
    masked = [e for e in items(client, mask=True) if e["source_file"] == "old.csv"][0]
    assert plain["network_identifiers"][0]["start"] is not None
    assert masked["network_identifiers"][0]["start"] is None and masked["network_identifiers"][0]["end"] is None


def test_facets_withhold_device_and_category_values_when_masking(env):
    client, _ = env
    plain = client.get("/api/logs/facets").json()
    masked = client.get("/api/logs/facets", params={"mask": True}).json()
    assert plain["categories"] and plain["devices"]
    assert masked["categories"] == [] and masked["devices"] == []
    assert masked["total"] == plain["total"] and masked["sources"] == plain["sources"]


def test_report_and_scan_cover_every_collection(env, monkeypatch):
    client, db = env
    _legacy(db, f"legacy {SSN}")
    body = client.get("/api/logs/privacy").json()
    assert (body["job_id"], body["events"], body["unscanned_events"], body["all_scanned"]) == ("all", 4, 1, False)
    assert body["complete_for_packaging"] is False
    assert client.post("/api/logs/privacy/scan").json() == {"scanned": 1}
    after = client.get("/api/logs/privacy").json()
    assert after["all_scanned"] and after["flagged_events"] == 3
    # "all" is complete only when every collection is; it never reads as complete by default.
    per_collection = [client.get(f"/api/logs/collections/{c['job_id']}/privacy").json()["ingest_complete"]
                      for c in client.get("/api/logs/collections").json()]
    assert after["ingest_complete"] is all(per_collection) and after["complete_for_packaging"] is all(per_collection)

    import app.storage.routes as routes
    real = routes.store.collection_summary

    def complete(db, job_id):
        summary = real(db, job_id)
        summary.complete = True
        return summary

    monkeypatch.setattr(routes.store, "collection_summary", complete)
    assert client.get("/api/logs/privacy").json()["complete_for_packaging"] is True


def test_an_empty_collection_is_not_reported_as_scanned(env):
    client, db = env
    db.add(Job(id="job-empty", mode="live", status="failed"))
    db.commit()
    body = client.get("/api/logs/collections/job-empty/privacy").json()
    assert body["events"] == 0 and body["all_scanned"] is False and body["complete_for_packaging"] is False


def test_a_collection_known_to_be_incomplete_cannot_be_certified(env, monkeypatch):
    client, _ = env
    import app.storage.routes as routes
    real = routes.store.collection_summary

    def incomplete(db, job_id):
        summary = real(db, job_id)
        summary.complete = False
        return summary

    monkeypatch.setattr(routes.store, "collection_summary", incomplete)
    body = client.get("/api/logs/collections/job-1/privacy").json()
    assert body["all_scanned"] is True and body["ingest_complete"] is False and body["complete_for_packaging"] is False


def test_a_scan_is_audited_when_it_starts_and_when_it_fails(env, monkeypatch):
    client, db = env
    import app.storage.routes as routes

    def boom(_db, _scope):
        raise RuntimeError("disk full")

    monkeypatch.setattr(routes.privacy, "rescan", boom)
    with pytest.raises(RuntimeError):
        client.post("/api/logs/collections/job-1/privacy/scan")
    entries = [(e.action, e.detail) for e in db.scalars(select(AuditLogEntry).order_by(AuditLogEntry.id))]
    assert ("logs_privacy_scan_start", "job=job-1") in entries
    assert ("logs_privacy_scan", "job=job-1 failed error=RuntimeError") in entries
    assert "disk full" not in repr(entries)
