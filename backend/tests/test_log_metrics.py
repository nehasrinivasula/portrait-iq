"""A-4: counts by severity, over time and by service (GET /api/logs/metrics, storage/metrics.py)."""
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
from app.main import app
from app.models import AuditLogEntry, Job
from app.models import NormalizedEvent as EventRow
from app.storage.filters import FilterError
from app.storage.metrics import MAX_BUCKETS, choose_bucket

T0 = datetime(2024, 6, 2, 10, 0, 0)
URL = "/api/logs/metrics"
LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]


@pytest.fixture
def env(monkeypatch):
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
    monkeypatch.setattr("app.auth.is_locked", lambda *_: False)
    seed = Session()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def add(db, minutes=0, n=1, **kw):
    """n events at T0 + minutes."""
    fields = dict(source="portrait", severity="INFO", message="hello", device_id="hub-1", category="General",
                  process="svc", job_id=None, source_file="a.csv", source_line=1)
    fields.update(kw)
    for i in range(n):
        db.add(EventRow(timestamp=T0 + timedelta(minutes=minutes, seconds=i), **fields))
    db.commit()


def get(client, **params):
    r = client.get(URL, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --- severity counts ------------------------------------------------------------------

def test_an_empty_store_gives_zero_counts_and_no_lists(env):
    client, _ = env
    body = get(client)
    assert body["total"] == 0 and body["severities"] == dict.fromkeys(LEVELS, 0)
    assert body["bucket"] is None and body["buckets"] == [] and body["groups"] == [] and body["other"] is None
    assert body["first_timestamp"] is None


def test_counts_by_severity_include_every_level_even_when_zero(env):
    client, db = env
    add(db, 0, 3, severity="ERROR")
    add(db, 1, 2, severity="WARN")
    add(db, 2, 5, severity="INFO")
    body = get(client)
    assert body["total"] == 10
    assert body["severities"] == {"DEBUG": 0, "INFO": 5, "WARN": 2, "ERROR": 3}
    assert body["first_timestamp"].startswith("2024-06-02T10:00:00")
    assert body["last_timestamp"].startswith("2024-06-02T10:02:04")


def test_counts_cover_only_the_period_asked_for(env):
    client, db = env
    add(db, 0, 2, severity="ERROR")                  # 10:00
    add(db, 60, 4, severity="ERROR")                 # 11:00
    add(db, 120, 8, severity="ERROR")                # 12:00
    since, until = (T0 + timedelta(minutes=30)).isoformat(), (T0 + timedelta(minutes=90)).isoformat()
    body = get(client, since=since, until=until)
    assert body["severities"]["ERROR"] == 4
    # the end of the period is exclusive, as in the log search
    assert get(client, since=T0.isoformat(), until=(T0 + timedelta(minutes=60)).isoformat())["severities"]["ERROR"] == 2


def test_the_other_filters_apply_to_the_counts(env):
    client, db = env
    add(db, 0, 3, severity="ERROR", device_id="hub-1")
    add(db, 1, 5, severity="ERROR", device_id="hub-2", source="kubernetes")
    assert get(client, device="hub-2")["total"] == 5
    assert get(client, source="kubernetes")["severities"]["ERROR"] == 5
    assert get(client, min_severity="ERROR")["total"] == 8
    assert get(client, severity="INFO")["total"] == 0


def test_counts_can_be_limited_to_one_collection(env):
    client, db = env
    db.add(Job(id="job-1", status="done"))
    db.commit()
    add(db, 0, 2, severity="ERROR", job_id="job-1")
    add(db, 1, 7, severity="ERROR")
    assert get(client, job_id="job-1")["total"] == 2
    assert get(client, job_id="unassigned")["total"] == 7


# --- over time ------------------------------------------------------------------------

def test_hour_buckets_fill_the_quiet_hours_with_zeros(env):
    client, db = env
    add(db, 0, 1, severity="ERROR")                  # 10:00
    add(db, 10, 1, severity="WARN")                  # 10:10
    add(db, 125, 1, severity="INFO")                 # 12:05, nothing in the 11:00 hour
    body = get(client, bucket="hour")
    assert body["bucket"] == "hour"
    starts = [b["start"][:13] for b in body["buckets"]]
    assert starts == ["2024-06-02T10", "2024-06-02T11", "2024-06-02T12"]
    assert [b["total"] for b in body["buckets"]] == [2, 0, 1]
    assert body["buckets"][0]["counts"] == {"DEBUG": 0, "INFO": 0, "WARN": 1, "ERROR": 1}
    assert sum(b["total"] for b in body["buckets"]) == body["total"]


def test_the_chart_covers_the_period_asked_for_not_just_the_events_found(env):
    client, db = env
    add(db, 0, 1)
    body = get(client, bucket="hour", since=(T0 - timedelta(hours=2)).isoformat(), until=(T0 + timedelta(hours=3)).isoformat())
    assert [b["total"] for b in body["buckets"]] == [0, 0, 1, 0, 0]


def test_auto_picks_minutes_hours_or_days_by_the_length_of_the_period(env):
    client, db = env
    add(db, 0, 1)
    add(db, 125, 1)                                   # two hours
    assert get(client)["bucket"] == "minute"
    add(db, 9 * 24 * 60, 1)                           # nine days
    assert get(client)["bucket"] == "hour"
    add(db, 30 * 24 * 60, 1)                          # thirty days
    assert get(client)["bucket"] == "day"


def test_a_bucket_that_would_make_too_many_bars_is_refused(env):
    client, db = env
    add(db, 0, 1)
    add(db, 3 * 24 * 60, 1)
    r = client.get(URL, params={"bucket": "minute"})
    assert r.status_code == 422 and "larger bucket" in r.json()["detail"]
    assert client.get(URL, params={"bucket": "week"}).status_code == 422


def test_choose_bucket_rules():
    start = datetime(2024, 6, 2, 10, 30)
    assert choose_bucket(start, start + timedelta(hours=2)) == "minute"
    assert choose_bucket(start, start + timedelta(days=5)) == "hour"
    assert choose_bucket(start, start + timedelta(days=60)) == "day"
    assert choose_bucket(start, start + timedelta(days=60), "day") == "day"
    with pytest.raises(FilterError):
        choose_bucket(start, start + timedelta(days=MAX_BUCKETS + 2))
    with pytest.raises(FilterError):
        choose_bucket(start, start + timedelta(days=2), "minute")


# --- by service -----------------------------------------------------------------------

def _services(db):
    add(db, 0, 3, severity="ERROR", process="svc-a")
    add(db, 1, 1, severity="WARN", process="svc-a")
    add(db, 2, 1, severity="ERROR", process="svc-b")
    add(db, 3, 5, severity="INFO", process="svc-b")
    add(db, 4, 2, severity="WARN", process="svc-c")
    add(db, 5, 1, severity="INFO", process=None)


def test_the_breakdown_lists_the_services_with_the_most_errors_first(env):
    client, db = env
    _services(db)
    body = get(client)
    assert body["group_by"] == "process" and body["groups_total"] == 4 and body["other"] is None
    assert [g["value"] for g in body["groups"]] == ["svc-a", "svc-b", "svc-c", None]
    a = body["groups"][0]
    assert a["counts"] == {"DEBUG": 0, "INFO": 0, "WARN": 1, "ERROR": 3} and a["total"] == 4
    assert body["groups"][1]["counts"]["INFO"] == 5


def test_only_the_top_groups_are_listed_and_the_rest_are_added_up(env):
    client, db = env
    _services(db)
    body = get(client, top=2)
    assert [g["value"] for g in body["groups"]] == ["svc-a", "svc-b"]
    assert body["groups_total"] == 4
    assert body["other"]["value"] is None and body["other"]["counts"] == {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 0}
    # nothing is lost: the listed groups and the rest add up to every severity count
    for level in LEVELS:
        assert sum(g["counts"][level] for g in body["groups"]) + body["other"]["counts"][level] == body["severities"][level]
    assert client.get(URL, params={"top": 0}).status_code == 422
    assert client.get(URL, params={"top": 51}).status_code == 422


def test_the_breakdown_can_group_by_other_fields(env):
    client, db = env
    add(db, 0, 4, severity="ERROR", device_id="hub-1", category="PerfData", source="portrait", component="Proc")
    add(db, 1, 2, severity="ERROR", device_id="hub-2", category="General", source="kubernetes")
    for group, first in (("device", "hub-1"), ("category", "PerfData"), ("source", "portrait"), ("component", "Proc")):
        body = get(client, group_by=group)
        assert body["group_by"] == group and body["groups"][0]["value"] == first, group
    assert client.get(URL, params={"group_by": "message"}).status_code == 422


def test_masking_withholds_group_values_that_can_hold_flagged_text(env):
    client, db = env
    _services(db)
    body = get(client, mask="true")
    assert body["withheld"] is True and body["groups"] == [] and body["other"] is None
    assert body["severities"]["ERROR"] == 4 and body["buckets"]          # counts and the chart are still there
    assert "svc-a" not in str(body)
    assert get(client, mask="true", group_by="source")["groups"][0]["value"] == "portrait"


# --- record keeping -------------------------------------------------------------------

def test_a_view_is_audited_with_filter_names_never_values(env):
    client, db = env
    add(db, 0, 1)
    get(client, q="patient-jane-doe", group_by="device")
    entry = db.scalars(select(AuditLogEntry).where(AuditLogEntry.action == "logs_metrics_view")).one()
    assert "text" in entry.detail and "group_by=device" in entry.detail
    assert "patient-jane-doe" not in entry.detail


def test_the_response_has_counts_only_no_log_text(env):
    client, db = env
    add(db, 0, 1, message="Patient Jane Doe, MRN 12345")
    assert "Jane" not in client.get(URL).text
