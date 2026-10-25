"""
Central log storage and the unified view, against an in-memory SQLite
database: one search across sources, stable paging, and proof that nothing
ingested goes missing from a collection.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, delete, func, inspect, select
from sqlalchemy.orm import sessionmaker

import app.db as dbmodule
from app.db import Base
from app.ingestion import pipeline
from app.models import AcquiredFileRecord, IngestedFile, Job
from app.models import NormalizedEvent as EventRow
from app.storage import events as store
from app.storage.filters import UNASSIGNED, FilterError, build_filter

T0 = datetime(2024, 6, 2, 10, 0, 0)
HEADER = (
    "timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,"
    "sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description"
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def add_event(db, seconds=0, **kw):
    fields = dict(
        timestamp=T0 + timedelta(seconds=seconds), source="portrait", severity="INFO",
        message="hello", device_id="hub-1", category="General", process="svc", job_id=None,
        source_file="a.csv", source_line=1,
    )
    fields.update(kw)
    row = EventRow(**fields)
    db.add(row)
    db.commit()
    return row


def make_job(db, job_id="job-1", status="done"):
    db.add(Job(id=job_id, status=status))
    db.commit()
    return job_id


def make_csv(path, good=3, bad=0, host="h1"):
    lines = [HEADER]
    for i in range(good):
        lines.append(
            f'"2024-06-02T04:{i // 60:02d}:{i % 60:02d}.000+0000","0","{i}","WARN","{host}","u",'
            f'"unsetFriendlyName","p[1:0]",":0","General","msg {i}"'
        )
    for _ in range(bad):
        lines.append('"garbage","0","0","INFO","h","u","x","p[1:0]",":0","General","bad"')
    path.write_text("\n".join(lines) + "\n")
    return path


def search(db, **kw):
    filters = {k: kw.pop(k) for k in list(kw) if k in build_filter.__kwdefaults__}
    return store.search_events(db, build_filter(**filters), **kw)


def messages(page):
    return [e.message for e in page.items]


# --- one search across every source and device ---------------------------------

def test_one_query_returns_matches_from_every_source_and_device_in_time_order(db):
    add_event(db, 2, source="portrait", device_id="hub-2", message="boot sequence done")
    add_event(db, 1, source="kubernetes", device_id="pod-a", message="Boot failed once", category=None)
    add_event(db, 0, source="portrait", device_id="hub-1", message="BOOT started")
    add_event(db, 3, source="portrait", device_id="hub-1", message="unrelated")

    page = search(db, text="boot", order="asc")

    assert messages(page) == ["BOOT started", "Boot failed once", "boot sequence done"]   # case-insensitive
    assert {e.source for e in page.items} == {"portrait", "kubernetes"}
    assert {e.device_id for e in page.items} == {"hub-1", "pod-a", "hub-2"}
    assert page.total == 3


def test_text_search_treats_percent_and_underscore_literally(db):
    add_event(db, 0, message="100% done")
    add_event(db, 1, message="100 done")
    add_event(db, 2, message="a_b")
    add_event(db, 3, message="axb")
    assert messages(search(db, text="100%")) == ["100% done"]
    assert messages(search(db, text="a_b")) == ["a_b"]


def test_every_filter_narrows_the_results(db):
    add_event(db, 0, device_id="hub-1", source="portrait", category="PerfData", severity="DEBUG", process="p1", source_file="x.csv", message="m0")
    add_event(db, 1, device_id="hub-2", source="portrait", category="General", severity="WARN", process="p2", source_file="y.csv", message="m1")
    add_event(db, 2, device_id="pod-a", source="kubernetes", category=None, severity="ERROR", process="p3", source_file="z.log", message="m2")

    assert messages(search(db, devices=["hub-1", "pod-a"], order="asc")) == ["m0", "m2"]
    assert messages(search(db, sources=["kubernetes"])) == ["m2"]
    assert messages(search(db, categories=["PerfData"])) == ["m0"]
    assert messages(search(db, severities=["WARN", "ERROR"], order="asc")) == ["m1", "m2"]
    assert messages(search(db, min_severity="warn", order="asc")) == ["m1", "m2"]
    assert messages(search(db, severities=["DEBUG"], min_severity="ERROR")) == []     # contradiction
    assert messages(search(db, process="p2")) == ["m1"]
    assert messages(search(db, source_file="z.log")) == ["m2"]
    assert messages(search(db, sources=["portrait"], min_severity="WARN")) == ["m1"]  # filters combine


def test_time_window_includes_since_and_excludes_until(db):
    for s in range(4):
        add_event(db, s, message=f"m{s}")
    page = search(db, since=T0 + timedelta(seconds=1), until=T0 + timedelta(seconds=3), order="asc")
    assert messages(page) == ["m1", "m2"]


def test_aware_times_are_converted_to_utc(db):
    add_event(db, 0, message="at ten utc")
    plus_two = timezone(timedelta(hours=2))
    page = search(db, since=datetime(2024, 6, 2, 12, 0, 0, tzinfo=plus_two), until=datetime(2024, 6, 2, 12, 0, 1, tzinfo=plus_two))
    assert messages(page) == ["at ten utc"]


def test_collection_and_unassigned_scopes(db):
    make_job(db)
    add_event(db, 0, job_id="job-1", message="in job")
    add_event(db, 1, job_id=None, message="ad hoc")
    assert messages(search(db, job_id="job-1")) == ["in job"]
    assert messages(search(db, job_id=UNASSIGNED)) == ["ad hoc"]
    assert search(db).total == 2


# --- paging --------------------------------------------------------------------

@pytest.mark.parametrize("order", ["asc", "desc"])
def test_paging_visits_every_event_once_even_with_identical_timestamps(db, order):
    for i in range(25):
        add_event(db, 0 if i < 15 else i, message=f"m{i}")      # 15 events share one timestamp
    f = build_filter()

    seen, cursor, pages = [], None, 0
    while True:
        page = store.search_events(db, f, limit=7, order=order, cursor=cursor)
        pages += 1
        seen += [e.id for e in page.items]
        if pages == 1:
            assert page.total == 25
        else:
            assert page.total is None            # counted once, on the first page
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert pages == 4 and len(seen) == 25 and len(set(seen)) == 25
    keys = [(e.timestamp, e.id) for e in db.scalars(select(EventRow).where(EventRow.id.in_(seen)))]
    by_id = {k[1]: k for k in keys}
    ordered = [by_id[i] for i in seen]
    assert ordered == sorted(ordered, reverse=(order == "desc"))


def test_a_full_last_page_has_no_next_cursor(db):
    for i in range(6):
        add_event(db, i)
    page = store.search_events(db, build_filter(), limit=6)
    assert len(page.items) == 6 and page.next_cursor is None


def test_new_events_arriving_between_pages_do_not_shift_the_next_page(db):
    for i in range(10):
        add_event(db, i, message=f"m{i}")
    first = store.search_events(db, build_filter(), limit=5, order="asc")
    add_event(db, -50, message="late arrival with an old timestamp")
    second = store.search_events(db, build_filter(), limit=5, order="asc", cursor=first.next_cursor)
    assert [e.message for e in second.items] == ["m5", "m6", "m7", "m8", "m9"]   # OFFSET paging would repeat m4


def test_limit_is_capped_and_bad_cursor_rejected(db):
    add_event(db)
    assert len(store.search_events(db, build_filter(), limit=10**9).items) == 1
    with pytest.raises(FilterError):
        store.search_events(db, build_filter(), cursor="nonsense")


# --- facets --------------------------------------------------------------------

def test_facets_count_what_is_in_the_result_set(db):
    add_event(db, 0, category="PerfData", severity="DEBUG", device_id="hub-1")
    add_event(db, 1, category="PerfData", severity="INFO", device_id="hub-1")
    add_event(db, 2, category="General", severity="INFO", device_id="hub-2")
    add_event(db, 3, category=None, severity="ERROR", device_id=None, source="kubernetes")

    r = store.facets(db, build_filter())
    assert r.total == 4 and r.distinct_devices == 2
    assert dict(r.categories) == {"PerfData": 2, "General": 1, None: 1}
    assert dict(r.severities) == {"INFO": 2, "DEBUG": 1, "ERROR": 1}
    assert dict(r.sources) == {"portrait": 3, "kubernetes": 1}
    assert r.first_timestamp == T0 and r.last_timestamp == T0 + timedelta(seconds=3)

    narrowed = store.facets(db, build_filter(sources=["portrait"]))
    assert narrowed.total == 3 and "kubernetes" not in dict(narrowed.sources)


def test_facets_on_an_empty_store(db):
    r = store.facets(db, build_filter())
    assert r.total == 0 and r.first_timestamp is None and r.devices == []


# --- unified view: nothing left out --------------------------------------------

def ingest_two_files(db, tmp_path, job_id="job-1", rows=(60, 45)):
    results = [
        pipeline.ingest_file(make_csv(tmp_path / "a.csv", good=rows[0], host="hub-1"), db, job_id=job_id),
        pipeline.ingest_file(make_csv(tmp_path / "b.csv", good=rows[1], host="hub-2"), db, job_id=job_id),
    ]
    return results


def test_ingest_leaves_a_receipt_per_file(db, tmp_path):
    make_job(db)
    ingest_two_files(db, tmp_path)
    receipts = {r.source_file: r for r in db.scalars(select(IngestedFile))}
    assert set(receipts) == {"a.csv", "b.csv"}
    a = receipts["a.csv"]
    assert (a.rows_read, a.events_written, a.rows_rejected, a.job_id, a.parser) == (60, 60, 0, "job-1", "portrait_csv")


def test_reingesting_replaces_the_receipt_instead_of_adding_one(db, tmp_path):
    make_job(db)
    ingest_two_files(db, tmp_path)
    ingest_two_files(db, tmp_path)
    assert db.scalar(select(func.count()).select_from(IngestedFile)) == 2
    assert store.collection_summary(db, "job-1").complete


def test_a_failed_file_leaves_no_receipt_and_no_events(db, tmp_path):
    bad = make_csv(tmp_path / "bad.csv", good=3, bad=3)
    with pytest.raises(pipeline.IngestError):
        pipeline.ingest_file(bad, db, job_id=None)
    assert db.scalar(select(func.count()).select_from(IngestedFile)) == 0
    assert db.scalar(select(func.count()).select_from(EventRow)) == 0


def test_every_ingested_row_is_visible_in_the_unified_view(db, tmp_path):
    make_job(db)
    ingest_two_files(db, tmp_path, rows=(60, 45))

    summary = store.collection_summary(db, "job-1")
    assert summary.complete and summary.issues == []
    assert (summary.rows_read, summary.events_expected, summary.events_stored) == (105, 105, 105)
    assert {f.source_file for f in summary.files} == {"a.csv", "b.csv"}

    # Walk the whole view in small pages: every event exactly once, none skipped.
    viewed = list(store.iter_events(db, build_filter(job_id="job-1"), page_size=10))
    assert len(viewed) == 105 == len({e.id for e in viewed})
    assert {e.source_file for e in viewed} == {"a.csv", "b.csv"}
    assert [(e.timestamp, e.id) for e in viewed] == sorted((e.timestamp, e.id) for e in viewed)


def test_rejected_rows_make_the_collection_incomplete_and_say_how_many(db, tmp_path):
    make_job(db)
    pipeline.ingest_file(make_csv(tmp_path / "a.csv", good=100, bad=1), db, job_id="job-1")    # 0.99% < 1% limit
    summary = store.collection_summary(db, "job-1")
    assert not summary.complete
    (issue,) = summary.issues
    assert issue.kind == "rows_rejected" and issue.file == "a.csv" and "1 of 101" in issue.message
    assert (summary.rows_read, summary.events_stored, summary.rows_rejected) == (101, 100, 1)


def test_events_removed_after_ingest_are_caught(db, tmp_path):
    make_job(db)
    ingest_two_files(db, tmp_path)
    ids = [r for r in db.scalars(select(EventRow.id).where(EventRow.source_file == "a.csv").limit(3))]
    db.execute(delete(EventRow).where(EventRow.id.in_(ids)))
    db.commit()

    summary = store.collection_summary(db, "job-1")
    assert not summary.complete
    (issue,) = summary.issues
    assert issue.kind == "store_mismatch" and issue.file == "a.csv"
    assert (summary.events_expected, summary.events_stored) == (105, 102)


def test_events_with_no_receipt_are_flagged(db):
    make_job(db)
    add_event(db, 0, job_id="job-1", source_file="legacy.csv")
    summary = store.collection_summary(db, "job-1")
    assert [i.kind for i in summary.issues] == ["untracked_events"] and not summary.complete


def test_files_that_failed_to_ingest_are_named(db, tmp_path):
    make_job(db)
    ingest_two_files(db, tmp_path)
    db.add(AcquiredFileRecord(source="local", remote_id="r9", name="broken.csv", status="failed",
                              error="1 of 2 rows could not be converted", job_id="job-1"))
    db.add(AcquiredFileRecord(source="local", remote_id="r10", name="same-as-a.csv", status="duplicate", job_id="job-1"))
    db.commit()

    summary = store.collection_summary(db, "job-1")
    assert not summary.complete
    problems = [i for i in summary.issues if i.severity == "problem"]
    assert [(i.kind, i.file) for i in problems] == [("file_not_stored", "broken.csv")]
    assert [i.kind for i in summary.issues if i.severity == "info"] == ["duplicate_file"]


def test_an_unfinished_or_empty_collection_is_not_complete(db, tmp_path):
    make_job(db, "running", status="running")
    make_job(db, "empty", status="done")
    assert [i.kind for i in store.collection_summary(db, "running").issues] == ["collection_running", "no_logs"]
    assert [i.kind for i in store.collection_summary(db, "empty").issues] == ["no_logs"]


def test_unknown_collection_is_none_and_unassigned_always_resolves(db):
    assert store.collection_summary(db, "no-such-job") is None
    assert store.collection_summary(db, UNASSIGNED) is not None


def test_header_only_file_is_receipted_as_zero_rows(db, tmp_path):
    make_job(db)
    empty = tmp_path / "empty.csv"
    empty.write_text(HEADER + "\n")
    pipeline.ingest_file(empty, db, job_id="job-1", allow_empty=True)
    summary = store.collection_summary(db, "job-1")
    assert summary.complete and summary.events_stored == 0 and summary.files[0].rows_read == 0


# --- listing collections -------------------------------------------------------

def test_list_collections_includes_empty_jobs_and_the_unassigned_bucket(db):
    make_job(db, "full")
    make_job(db, "empty", status="failed")
    add_event(db, 0, job_id="full")
    add_event(db, 1, job_id="full")
    add_event(db, 2, job_id=None)

    rows = {c.job_id: c for c in store.list_collections(db)}
    assert rows["full"].events == 2 and rows["empty"].events == 0 and rows["empty"].status == "failed"
    assert rows[UNASSIGNED].events == 1
    assert rows["full"].first_timestamp == T0 and rows["full"].last_timestamp == T0 + timedelta(seconds=1)


# --- indexes on databases that already exist -----------------------------------

def test_indexes_are_added_to_a_database_created_before_they_existed(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX ix_events_job_ts")
        conn.exec_driver_sql("DROP INDEX ix_events_device_ts")
    monkeypatch.setattr(dbmodule, "engine", engine)

    dbmodule._create_missing_indexes()
    dbmodule._create_missing_indexes()       # safe to run on every startup

    names = {i["name"] for i in inspect(engine).get_indexes("normalized_events")}
    assert {"ix_events_job_ts", "ix_events_device_ts"} <= names
