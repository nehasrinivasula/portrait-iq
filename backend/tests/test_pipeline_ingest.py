"""
Database side of ingestion: atomic, idempotent, loud. Uses an in-memory
SQLite database, so it needs no setup beyond `pip install -r requirements.txt`.
"""
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.ingestion import pipeline
from app.ingestion.parsers.base import UnrecognizedLogFormat
from app.models import NormalizedEvent as EventRow

HEADER = (
    "timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,"
    "sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description"
)


def make_csv(path, good=3, bad=0, tz="+0200"):
    lines = [HEADER]
    for i in range(good):
        lines.append(f'"2024-06-02T04:00:0{i}.000{tz}","0","{i}","WARN","h{i}","u","unsetFriendlyName","p[1:0]",":0","General","msg {i}"')
    for _ in range(bad):
        lines.append('"garbage","0","0","INFO","h","u","x","p[1:0]",":0","General","bad"')
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def count(db):
    return db.scalar(select(func.count()).select_from(EventRow))


def test_ingest_stores_events_with_utc_and_provenance(db, tmp_path):
    result = pipeline.ingest_file(make_csv(tmp_path / "a.csv", good=3, tz="+0200"), db)
    assert result.ok and result.events_written == 3 and result.parser == "portrait_csv"

    first = db.scalars(select(EventRow).order_by(EventRow.source_line)).first()
    # 04:00:00+02:00 is 02:00:00 UTC; stored naive, but in UTC
    assert first.timestamp.isoformat().startswith("2024-06-02T02:00:00")
    assert first.timestamp.tzinfo is None     # stored naive-UTC by design
    assert first.source == "portrait" and first.severity == "WARN"
    assert first.source_file == "a.csv" and first.source_line == 2


def test_reingesting_the_same_file_does_not_duplicate(db, tmp_path):
    path = make_csv(tmp_path / "a.csv", good=3)
    pipeline.ingest_file(path, db)
    pipeline.ingest_file(path, db)
    assert count(db) == 3


def test_reingest_replaces_old_rows_for_that_file_only(db, tmp_path):
    pipeline.ingest_file(make_csv(tmp_path / "a.csv", good=3), db)
    pipeline.ingest_file(make_csv(tmp_path / "b.csv", good=2), db)
    pipeline.ingest_file(make_csv(tmp_path / "a.csv", good=1), db)   # a shrinks to 1 row
    assert count(db) == 3          # 1 (a) + 2 (b)


def test_too_many_bad_rows_stores_nothing_and_raises(db, tmp_path):
    path = make_csv(tmp_path / "a.csv", good=3, bad=3)               # 50% bad
    with pytest.raises(pipeline.IngestError):
        pipeline.ingest_file(path, db)
    assert count(db) == 0


def test_failed_reingest_keeps_the_previous_good_data(db, tmp_path):
    path = make_csv(tmp_path / "a.csv", good=3)
    pipeline.ingest_file(path, db)
    make_csv(path, good=1, bad=5)                                    # now corrupt
    with pytest.raises(pipeline.IngestError):
        pipeline.ingest_file(path, db)
    assert count(db) == 3          # rolled back, old rows intact


def test_unrecognised_file_raises(db, tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n")
    with pytest.raises(UnrecognizedLogFormat):
        pipeline.ingest_file(p, db)


def test_ingest_directory_continues_past_a_bad_file_and_reports_it(db, tmp_path):
    make_csv(tmp_path / "good1.csv", good=2)
    make_csv(tmp_path / "good2.csv", good=2)
    (tmp_path / "junk.csv").write_text("a,b\n1,2\n")
    results = {r.source_file: r for r in pipeline.ingest_directory(tmp_path, db)}
    assert results["good1.csv"].ok and results["good2.csv"].ok
    assert not results["junk.csv"].ok and results["junk.csv"].error
    assert count(db) == 4


def test_job_scoped_ingest_does_not_touch_other_jobs(db, tmp_path):
    path = make_csv(tmp_path / "a.csv", good=3)
    pipeline.ingest_file(path, db, job_id=None)
    pipeline.ingest_file(path, db, job_id="job-1")
    pipeline.ingest_file(path, db, job_id="job-1")                    # replace within job-1 only
    assert count(db) == 6
