"""
/api/logs end to end through FastAPI's TestClient, with the real routes and an
in-memory database. Sign-in is replaced by a fixed user so these tests cover
the log endpoints, not login (see test_auth.py for that).
"""
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
from app.ingestion import pipeline
from app.main import app
from app.models import AuditLogEntry, Job
from app.models import NormalizedEvent as EventRow

T0 = datetime(2024, 6, 2, 10, 0, 0)
HEADER = (
    "timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,"
    "sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description"
)


@pytest.fixture
def env():
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
    # No `with`: that would run the app's startup and create the real portrait_iq.db.
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def add_event(db, seconds=0, **kw):
    fields = dict(
        timestamp=T0 + timedelta(seconds=seconds), source="portrait", severity="INFO", message="hello",
        device_id="hub-1", category="General", process="svc", job_id=None, source_file="a.csv", source_line=1,
    )
    fields.update(kw)
    db.add(EventRow(**fields))
    db.commit()


def make_csv(path, good, host="h1"):
    lines = [HEADER] + [
        f'"2024-06-02T04:{i // 60:02d}:{i % 60:02d}.000+0000","0","{i}","WARN","{host}","u",'
        f'"unsetFriendlyName","p[1:0]",":0","General","msg {i}"'
        for i in range(good)
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def walk(client, url, **params):
    """Follow next_cursor to the end; return every item and the first page's total."""
    items, total, cursor = [], None, None
    while True:
        p = dict(params, limit=40, **({"cursor": cursor} if cursor else {}))
        body = client.get(url, params=p).json()
        if cursor is None:
            total = body["total"]
        items += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items, total


# --- access --------------------------------------------------------------------

def test_every_log_route_needs_a_signed_in_user(env):
    client, _ = env
    app.dependency_overrides.pop(get_current_user)
    for url in ("/api/logs", "/api/logs/events", "/api/logs/facets", "/api/logs/collections",
                "/api/logs/collections/x", "/api/logs/collections/x/events"):
        r = client.get(url)
        assert r.status_code == 401, url
        assert r.json()["code"] == "auth_required"


def test_a_locked_session_cannot_read_logs(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr("app.storage.routes.is_locked", lambda user_id: True)
    assert client.get("/api/logs/events").status_code == 423
    assert client.get("/api/logs").status_code == 423


def test_scrum53_offset_api_filters_exactly_and_pages(env):
    client, db = env
    add_event(db, seconds=0, device_id="hub-1", severity="ERROR", category="General")
    add_event(db, seconds=1, device_id="hub-1", severity="ERROR", category="General")
    add_event(db, seconds=2, device_id="hub-1", severity="WARN", category="PerfData")
    add_event(db, seconds=3, device_id="hub-2", severity="ERROR", category="General")
    window = {"start": "2024-06-02T10:00:00Z", "end": "2024-06-02T10:00:03Z"}
    all_logs = client.get("/api/logs", params=window).json()
    assert all_logs["total"] == 3
    params = {**window, "device_id": "hub-1", "severity": "ERROR", "category": "General"}
    first = client.get("/api/logs", params={**params, "limit": 1, "offset": 0}).json()
    second = client.get("/api/logs", params={**params, "limit": 1, "offset": 1}).json()
    assert first["total"] == second["total"] == 2
    assert first["limit"] == second["limit"] == 1
    assert (first["offset"], second["offset"]) == (0, 1)
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {
        item["id"] for item in all_logs["items"] if item["device_id"] == "hub-1" and item["severity"] == "ERROR"
    }
    assert client.get("/api/logs", params={**params, "device_id": "missing"}).json()["total"] == 0
    assert client.get("/api/logs", params={**window, "category": "PerfData"}).json()["total"] == 1


@pytest.mark.parametrize("params", [
    {"severity": "CRITICAL"},
    {"severity": "error"},
    {"device_id": " "},
    {"category": " "},
    {"start": "2024-06-02T10:01:00Z", "end": "2024-06-02T10:00:00Z"},
    {"limit": 0},
    {"offset": -1},
])
def test_scrum53_offset_api_rejects_invalid_filters(env, params):
    client, _ = env
    assert client.get("/api/logs", params=params).status_code == 422


# --- search --------------------------------------------------------------------

def test_search_returns_newest_first_with_utc_timestamps_and_pages(env):
    client, db = env
    for i in range(5):
        add_event(db, i, message=f"m{i}")

    first = client.get("/api/logs/events", params={"limit": 2}).json()
    assert [e["message"] for e in first["items"]] == ["m4", "m3"]
    assert first["total"] == 5 and first["next_cursor"]
    assert first["items"][0]["timestamp"].endswith("Z")
    assert set(first["items"][0]) >= {"id", "device_id", "source", "category", "severity", "process",
                                      "message", "source_file", "source_line", "job_id"}

    second = client.get("/api/logs/events", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [e["message"] for e in second["items"]] == ["m2", "m1"]
    assert second["total"] is None


def test_filters_work_across_sources_in_one_request(env):
    client, db = env
    add_event(db, 0, source="portrait", device_id="hub-1", severity="ERROR", message="Disk error")
    add_event(db, 1, source="kubernetes", device_id="pod-a", severity="ERROR", message="disk pressure", category=None)
    add_event(db, 2, source="kubernetes", device_id="pod-a", severity="INFO", message="disk fine")

    body = client.get("/api/logs/events", params={"q": "DISK", "min_severity": "warn", "order": "asc"}).json()
    assert [e["message"] for e in body["items"]] == ["Disk error", "disk pressure"]
    assert {e["source"] for e in body["items"]} == {"portrait", "kubernetes"}

    body = client.get("/api/logs/events", params=[("device", "hub-1"), ("device", "pod-a"), ("severity", "INFO")]).json()
    assert [e["message"] for e in body["items"]] == ["disk fine"]


def test_since_and_until_accept_offsets(env):
    client, db = env
    add_event(db, 0, message="ten utc")
    r = client.get("/api/logs/events", params={"since": "2024-06-02T12:00:00+02:00", "until": "2024-06-02T12:00:01+02:00"})
    assert [e["message"] for e in r.json()["items"]] == ["ten utc"]


@pytest.mark.parametrize(
    "params, status",
    [
        ({"severity": "loud"}, 400),
        ({"min_severity": "loud"}, 400),
        ({"since": "2024-06-02T10:00:00", "until": "2024-06-02T09:00:00"}, 400),
        ({"cursor": "garbage"}, 400),
        ({"limit": 0}, 422),
        ({"limit": 5000}, 422),
        ({"order": "sideways"}, 422),
        ({"since": "yesterday-ish"}, 422),
    ],
)
def test_bad_parameters_are_refused_with_a_reason(env, params, status):
    client, _ = env
    assert client.get("/api/logs/events", params=params).status_code == status


def test_facets_endpoint(env):
    client, db = env
    add_event(db, 0, category="PerfData")
    add_event(db, 1, category="PerfData")
    add_event(db, 2, category="General", source="kubernetes")
    body = client.get("/api/logs/facets").json()
    assert body["total"] == 3
    assert {c["value"]: c["count"] for c in body["categories"]} == {"PerfData": 2, "General": 1}
    assert {c["value"]: c["count"] for c in client.get("/api/logs/facets", params={"source": "kubernetes"}).json()["categories"]} == {"General": 1}


# --- unified view --------------------------------------------------------------

def test_collection_endpoints_for_an_unknown_job_are_404(env):
    client, _ = env
    assert client.get("/api/logs/collections/nope").status_code == 404
    assert client.get("/api/logs/collections/nope/events").status_code == 404


def test_unified_view_shows_every_entry_of_a_finished_collection(env, tmp_path):
    client, db = env
    db.add(Job(id="job-1", status="done"))
    db.commit()
    pipeline.ingest_file(make_csv(tmp_path / "a.csv", 130, host="hub-1"), db, job_id="job-1")
    pipeline.ingest_file(make_csv(tmp_path / "b.csv", 95, host="hub-2"), db, job_id="job-1")
    add_event(db, 0, job_id=None, message="not in this collection")

    summary = client.get("/api/logs/collections/job-1").json()
    assert summary["complete"] is True and summary["issues"] == []
    assert summary["events_stored"] == summary["events_expected"] == summary["rows_read"] == 225
    assert {f["source_file"] for f in summary["files"]} == {"a.csv", "b.csv"}

    items, total = walk(client, "/api/logs/collections/job-1/events")
    assert total == 225 == len(items) == len({e["id"] for e in items})     # none missing, none repeated
    assert all(e["job_id"] == "job-1" for e in items)
    stamps = [(e["timestamp"], e["id"]) for e in items]
    assert stamps == sorted(stamps)                                         # one time-ordered stream
    assert {e["source_file"] for e in items} == {"a.csv", "b.csv"}


def test_incomplete_collection_says_so_and_says_why(env, tmp_path):
    client, db = env
    db.add(Job(id="job-1", status="running"))
    db.commit()
    pipeline.ingest_file(make_csv(tmp_path / "a.csv", 10), db, job_id="job-1")
    db.query(EventRow).filter(EventRow.source_line == 2).delete()
    db.commit()

    summary = client.get("/api/logs/collections/job-1").json()
    assert summary["complete"] is False
    assert {i["kind"] for i in summary["issues"]} == {"collection_running", "store_mismatch"}
    assert summary["events_expected"] == 10 and summary["events_stored"] == 9


def test_view_accepts_the_same_filters_and_order(env):
    client, db = env
    db.add(Job(id="job-1", status="done"))
    db.commit()
    add_event(db, 0, job_id="job-1", category="PerfData", message="noise")
    add_event(db, 1, job_id="job-1", category="General", message="signal")
    add_event(db, 2, job_id=None, category="General", message="elsewhere")

    body = client.get("/api/logs/collections/job-1/events", params={"category": "General"}).json()
    assert [e["message"] for e in body["items"]] == ["signal"]
    body = client.get("/api/logs/collections/job-1/events", params={"order": "desc"}).json()
    assert [e["message"] for e in body["items"]] == ["signal", "noise"]


def test_collections_list_and_unassigned_bucket(env):
    client, db = env
    db.add(Job(id="job-1", status="done"))
    db.commit()
    add_event(db, 0, job_id="job-1")
    add_event(db, 1, job_id=None, message="loose")

    rows = {c["job_id"]: c for c in client.get("/api/logs/collections").json()}
    assert rows["job-1"]["events"] == 1 and rows["job-1"]["status"] == "done"
    assert rows["unassigned"]["events"] == 1

    body = client.get("/api/logs/collections/unassigned/events").json()
    assert [e["message"] for e in body["items"]] == ["loose"]
    assert client.get("/api/logs/collections/unassigned").json()["events_stored"] == 1


# --- audit and privacy ---------------------------------------------------------

def test_search_is_audited_without_the_search_text(env):
    client, db = env
    add_event(db, 0, message="patient 4471 note")
    client.get("/api/logs/events", params={"q": "4471"})

    entries = list(db.scalars(select(AuditLogEntry).where(AuditLogEntry.action == "logs_search")))
    assert len(entries) == 1 and entries[0].actor == "tester"
    assert "text" in entries[0].detail and "4471" not in entries[0].detail
    assert "patient" not in entries[0].detail and "matched=1" in entries[0].detail


def test_paging_through_results_audits_once(env):
    client, db = env
    for i in range(5):
        add_event(db, i)
    walk(client, "/api/logs/events")
    walk(client, "/api/logs/events", order="asc")
    assert len(list(db.scalars(select(AuditLogEntry).where(AuditLogEntry.action == "logs_search")))) == 2
