"""PHI/PII flagging in the database: flags written at ingest, the per-collection
report, rescanning, and the search filters. In-memory SQLite."""
from datetime import datetime

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.ingestion import pipeline
from app.models import Job
from app.models import NormalizedEvent as EventRow
from app.privacy import scanner, service
from app.storage import events as store
from app.storage.filters import FilterError, UNASSIGNED, build_filter

HEADER = (
    "timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,"
    "sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description"
)
SSN, NAME, EMAIL = "123-45-6789", "John Smith", "jane.doe@hospital.org"


def write_csv(path, messages):
    lines = [HEADER] + [
        f'"2024-06-02T04:00:{i:02d}.000+0000","0","{i}","INFO","host-a","u","unsetFriendlyName",'
        f'"svc[1:0]",":0","General","{m}"'
        for i, m in enumerate(messages)
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def ingested(db, tmp_path):
    db.add(Job(id="job-1", mode="live", status="done"))
    db.commit()
    path = write_csv(tmp_path / "a.csv", [
        "all quiet",
        f"patientName={NAME} admitted",
        f"billing ssn {SSN} and mail {EMAIL}",
        "ping scheduler timed out",
    ])
    pipeline.ingest_file(path, db, job_id="job-1")
    return db


def rows(db):
    return list(db.scalars(select(EventRow).order_by(EventRow.source_line)))


def test_every_ingested_event_is_stamped_with_the_scanner_version(ingested):
    assert {r.privacy_scan_version for r in rows(ingested)} == {scanner.SCANNER_VERSION}


def test_ingest_flags_known_patterns_and_leaves_clean_events_unflagged(ingested):
    quiet, name, billing, ping = rows(ingested)
    assert quiet.sensitive_flags is None and ping.sensitive_flags is None
    assert [f.category for f in scanner.decode_flags(name.sensitive_flags)] == ["person_name"]
    assert sorted(f.category for f in scanner.decode_flags(billing.sensitive_flags)) == ["email", "ssn"]


def test_stored_flags_hold_positions_not_the_sensitive_text(ingested):
    for r in rows(ingested):
        for secret in (NAME, SSN, EMAIL, "Smith"):
            assert secret not in (r.sensitive_flags or "")


def test_flag_positions_point_at_the_original_message(ingested):
    name = rows(ingested)[1]
    f, = scanner.decode_flags(name.sensitive_flags)
    assert name.message[f.start:f.end] == NAME


def test_reingesting_a_file_replaces_flags_instead_of_duplicating(ingested, tmp_path):
    path = write_csv(tmp_path / "a.csv", ["all quiet"])
    pipeline.ingest_file(path, ingested, job_id="job-1")
    r, = rows(ingested)
    assert r.sensitive_flags is None


def test_report_counts_flags_without_returning_text(ingested):
    r = service.privacy_report(ingested, "job-1")
    assert (r.events, r.flagged_events, r.unscanned_events) == (4, 2, 0)
    assert r.all_scanned
    assert r.flags_by_category == {"person_name": 1, "email": 1, "ssn": 1}
    assert r.flags_by_field == {"message": 3}
    assert r.flags_by_confidence == {"high": 3}
    f, = r.files
    assert (f.source_file, f.events, f.flagged_events, f.unscanned_events) == ("a.csv", 4, 2, 0)
    assert NAME not in repr(r) and SSN not in repr(r)


def test_events_never_scanned_make_the_report_say_so(ingested):
    ingested.add(EventRow(timestamp=datetime(2024, 6, 2, 5), source="portrait", severity="INFO",
                          message=f"old row {SSN}", job_id="job-1", source_file="old.csv", source_line=1))
    ingested.commit()
    r = service.privacy_report(ingested, "job-1")
    assert r.unscanned_events == 1 and not r.all_scanned
    assert r.scanned_events == 4


def test_events_scanned_by_an_older_scanner_count_as_unscanned(ingested):
    first = rows(ingested)[0]
    first.privacy_scan_version = scanner.SCANNER_VERSION - 1
    ingested.commit()
    assert service.privacy_report(ingested, "job-1").unscanned_events == 1


def test_rescan_judges_old_events_and_is_repeatable(ingested):
    ingested.add(EventRow(timestamp=datetime(2024, 6, 2, 5), source="portrait", severity="INFO",
                          message=f"old row {SSN}", job_id="job-1", source_file="old.csv", source_line=1))
    ingested.commit()
    assert service.rescan(ingested, "job-1") == 1
    assert service.rescan(ingested, "job-1") == 0
    r = service.privacy_report(ingested, "job-1")
    assert r.all_scanned and r.flagged_events == 3 and r.flags_by_category["ssn"] == 2


def test_rescan_with_a_collection_leaves_other_collections_alone(ingested):
    ingested.add(EventRow(timestamp=datetime(2024, 6, 2, 5), source="portrait", severity="INFO",
                          message=f"adhoc {SSN}", job_id=None, source_file="adhoc.csv", source_line=1))
    ingested.commit()
    assert service.rescan(ingested, "job-1") == 0
    assert service.privacy_report(ingested, UNASSIGNED).unscanned_events == 1
    assert service.rescan(ingested, UNASSIGNED) == 1


def test_report_for_an_empty_collection_is_all_zero(db):
    r = service.privacy_report(db, "nothing-here")
    # Nothing was scanned, so it must not pass as "all scanned": a package built from it would carry no evidence.
    assert (r.events, r.flagged_events, r.unscanned_events) == (0, 0, 0) and not r.all_scanned


# source_line counts the file's lines, and line 1 is the header: the four messages sit on lines 2-5
# (quiet, name, billing, ping).
def search(db, **kw):
    return [e.source_line for e in store.search_events(db, build_filter(**kw), order="asc").items]


def test_filter_flagged_true_returns_only_flagged_events(ingested):
    assert search(ingested, flagged=True) == [3, 4]


def test_filter_flagged_false_returns_scanned_clean_events_only(ingested):
    ingested.add(EventRow(timestamp=datetime(2024, 6, 2, 5), source="portrait", severity="INFO",
                          message="never scanned", job_id="job-1", source_file="old.csv", source_line=9))
    ingested.commit()
    assert search(ingested, flagged=False) == [2, 5]      # the unscanned row is not "clean"


def test_filter_by_flag_category(ingested):
    assert search(ingested, flag_categories=["ssn"]) == [4]
    assert search(ingested, flag_categories=["person_name", "email"]) == [3, 4]
    assert search(ingested, flag_categories=["credit_card"]) == []


def test_unknown_flag_category_is_rejected(ingested):
    with pytest.raises(FilterError):
        build_filter(flag_categories=["nonsense"])


def test_audit_field_names_include_the_flag_filters_but_no_values():
    f = build_filter(flagged=True, flag_categories=["ssn"])
    assert f.used_fields() == ["flagged", "flag_category"]
    assert build_filter(flagged=False).used_fields() == ["flagged"]


def add_network_event(db, line=20):
    db.add(EventRow(timestamp=datetime(2024, 6, 2, 5), source="portrait", severity="INFO",
                    message="timeout talking to 10.0.0.5", device_id="00:1A:2B:3C:4D:5E", job_id="job-1",
                    source_file="net.csv", source_line=line, **scanner.scan_columns(
                        message="timeout talking to 10.0.0.5", device_id="00:1A:2B:3C:4D:5E")))
    db.commit()


def test_ip_and_mac_addresses_are_stored_apart_and_do_not_count_as_flagged(ingested):
    add_network_event(ingested)
    r = [e for e in rows(ingested) if e.source_file == "net.csv"][0]
    assert r.sensitive_flags is None
    assert {f.category for f in scanner.decode_flags(r.network_flags)} == {"ip_address", "mac_address"}
    assert 20 not in search(ingested, flagged=True)
    assert 20 in search(ingested, flagged=False)          # scanned, no personal data


def test_report_counts_network_identifiers_separately(ingested):
    add_network_event(ingested)
    r = service.privacy_report(ingested, "job-1")
    assert r.flagged_events == 2 and r.network_events == 1
    assert r.network_by_category == {"ip_address": 1, "mac_address": 1}
    assert "ip_address" not in r.flags_by_category and "mac_address" not in r.flags_by_category
    assert {f.source_file: f.network_events for f in r.files}["net.csv"] == 1


def test_flag_category_filter_finds_network_kinds_too(ingested):
    add_network_event(ingested)
    assert search(ingested, flag_categories=["ip_address"]) == [20]
    assert search(ingested, flag_categories=["mac_address", "ssn"]) == [4, 20]


def test_rescan_fills_in_both_flag_columns(ingested):
    ingested.add(EventRow(timestamp=datetime(2024, 6, 2, 6), source="portrait", severity="INFO",
                          message=f"old {SSN} via 10.0.0.5", job_id="job-1", source_file="old.csv", source_line=1))
    ingested.commit()
    service.rescan(ingested, "job-1")
    r = [e for e in rows(ingested) if e.source_file == "old.csv"][0]
    assert [f.category for f in scanner.decode_flags(r.sensitive_flags)] == ["ssn"]
    assert [f.category for f in scanner.decode_flags(r.network_flags)] == ["ip_address"]


def test_flagged_false_excludes_events_judged_by_an_older_scanner(ingested):
    assert search(ingested, flagged=False) == [2]
    first = rows(ingested)[0]                                  # the one clean event
    first.privacy_scan_version = scanner.SCANNER_VERSION - 1
    ingested.commit()
    # The report counts it as unscanned, so the filter must not call it clean either.
    assert service.privacy_report(ingested, "job-1").unscanned_events == 1
    assert search(ingested, flagged=False) == []


def test_report_and_rescan_can_cover_every_collection(ingested):
    ingested.add(EventRow(timestamp=datetime(2024, 6, 2, 5), source="portrait", severity="INFO",
                          message=f"adhoc {SSN}", job_id=None, source_file="adhoc.csv", source_line=1))
    ingested.commit()
    r = service.privacy_report(ingested, None)
    assert (r.job_id, r.events, r.unscanned_events, r.all_scanned) == ("all", 5, 1, False)
    assert service.rescan(ingested, None) == 1
    r = service.privacy_report(ingested, None)
    assert r.all_scanned and r.flagged_events == 3


def test_upgrading_an_older_database_keeps_its_events_and_leaves_them_to_rescan(tmp_path, monkeypatch):
    """Databases created before flagging existed get the three columns added in place; their events then
    count as unscanned and a rescan judges them. Nothing needs deleting."""
    from sqlalchemy import inspect, text
    from app import db as dbmodule

    old_engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(old_engine)
    with old_engine.begin() as connection:
        for column in ("privacy_scan_version", "sensitive_flags", "network_flags"):
            connection.execute(text(f"ALTER TABLE normalized_events DROP COLUMN {column}"))
        connection.execute(text(
            "INSERT INTO normalized_events (timestamp, source, severity, message) "
            f"VALUES ('2024-06-02 04:00:00', 'portrait', 'INFO', 'old row {SSN}')"))

    monkeypatch.setattr(dbmodule, "engine", old_engine)
    dbmodule.init_db()

    names = {c["name"] for c in inspect(old_engine).get_columns("normalized_events")}
    assert {"privacy_scan_version", "sensitive_flags", "network_flags"} <= names
    session = sessionmaker(bind=old_engine)()
    try:
        report = service.privacy_report(session, None)
        assert (report.events, report.unscanned_events) == (1, 1)
        assert service.rescan(session, None) == 1
        assert service.privacy_report(session, None).flagged_events == 1
    finally:
        session.close()
