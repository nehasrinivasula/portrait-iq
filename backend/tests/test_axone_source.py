"""Axone log export source: hour windows, endpoint choice, and the full pull
through acquire() into the database. Runs against tests/axone_fake.py."""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.ingestion.parsers import portrait_csv
from app.models import AcquiredFileRecord, NormalizedEvent
from app.sources import registry
from app.sources.axone_client import AxoneLogExportClient
from app.sources.axone_export import AxoneLogExportSource
from app.sources.base import SourceError
from app.sources.service import acquire, resolve_window
from tests.axone_fake import FakeAxone, Row
from tests.conftest import PORTRAIT_CSV

DEV = "d4e5f6a7-1234-5678-9abc-def012345678"
NOW_HOUR = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
BASE = NOW_HOUR - timedelta(hours=5)   # three complete hours: BASE, BASE+1h, BASE+2h


def naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None)


@pytest.fixture
def axone_settings(monkeypatch):
    monkeypatch.setattr(settings, "axone_base_url", "http://fake-axone:9200")
    monkeypatch.setattr(settings, "axone_timezone", "UTC")
    monkeypatch.setattr(settings, "axone_request_delay_seconds", 0.25)


@pytest.fixture
def fake():
    # 2 rows in the first hour, 0 in the second, 3 in the third.
    return FakeAxone(rows=[
        Row(naive(BASE + timedelta(minutes=5))),
        Row(naive(BASE + timedelta(minutes=50)), category=3, level="ERROR", message="NullPointerException"),
        Row(naive(BASE + timedelta(hours=2, minutes=1))),
        Row(naive(BASE + timedelta(hours=2, minutes=2)), level="WARN"),
        Row(naive(BASE + timedelta(hours=2, minutes=59, seconds=59))),
    ])


@pytest.fixture
def source(fake, axone_settings):
    sleeps = []
    src = AxoneLogExportSource(
        client_factory=lambda: AxoneLogExportClient("http://fake-axone:9200", transport=fake.transport()),
        sleep=sleeps.append,
    )
    src.sleeps = sleeps
    return src


# --- configuration -----------------------------------------------------------------

def test_not_set_up_without_a_url(monkeypatch):
    monkeypatch.setattr(settings, "axone_base_url", None)
    status = AxoneLogExportSource().is_configured()
    assert not status.ok and "AXONE_BASE_URL" in status.reason
    with pytest.raises(SourceError, match="AXONE_BASE_URL"):
        AxoneLogExportSource().fetch(None, None)


def test_registered_and_defaults_to_24_hours(axone_settings):
    src = registry.get_source("axone")
    assert isinstance(src, AxoneLogExportSource) and src.is_configured().ok
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    since, until = resolve_window(None, None, now=now, default_lookback=src.default_lookback)
    assert until - since == timedelta(hours=24)


def test_connection_check(source, fake):
    assert source.test_connection().ok
    probe = fake.calls()[-1]
    assert probe.url.path == "/getAllDeviceLogsInCSV"

    # Make the API fail for the hour the probe asks about.
    probe_hour = datetime.fromisoformat(probe.url.params["startTimestamp"]).replace(minute=0, second=0, microsecond=0)
    fake.fail_hours.add(probe_hour)
    status = source.test_connection()
    assert not status.ok and "internal error" in status.reason


# --- planning windows ------------------------------------------------------------------

LATER = lambda: datetime(2026, 12, 1, tzinfo=timezone.utc)   # a clock far after the windows below


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_exact_window_is_cut_at_hour_boundaries():
    # SCRUM-44: 22:30 to 02:10 means exactly that, split so each piece is in one hour.
    src = AxoneLogExportSource(client_factory=lambda: None, clock=LATER)
    windows = src.list_available(utc(2026, 10, 18, 22, 30), utc(2026, 10, 19, 2, 10), {})
    assert [(w.window_start, w.window_end) for w in windows] == [
        (utc(2026, 10, 18, 22, 30), utc(2026, 10, 18, 22, 59, 59, 999000)),
        (utc(2026, 10, 18, 23), utc(2026, 10, 18, 23, 59, 59, 999000)),
        (utc(2026, 10, 19, 0), utc(2026, 10, 19, 0, 59, 59, 999000)),
        (utc(2026, 10, 19, 1), utc(2026, 10, 19, 1, 59, 59, 999000)),
        (utc(2026, 10, 19, 2), utc(2026, 10, 19, 2, 9, 59, 999000)),
    ]
    assert windows[0].remote_id == "alldevices|2026-10-18T22:30:00+00:00|2026-10-18T23:00:00+00:00"
    assert windows[0].name == "axone_alldevices_2026-10-18T223000_2026-10-18T230000.csv"


def test_a_window_inside_one_hour_is_one_request():
    src = AxoneLogExportSource(client_factory=lambda: None, clock=LATER)
    windows = src.list_available(utc(2026, 10, 19, 9, 15), utc(2026, 10, 19, 9, 45), {})
    assert [(w.window_start, w.window_end) for w in windows] == [
        (utc(2026, 10, 19, 9, 15), utc(2026, 10, 19, 9, 44, 59, 999000))]


def test_pull_reaches_now_minus_the_settle_time(monkeypatch):
    # At 14:40 with a 15 minute settle, a pull up to "now" stops at 14:25.
    monkeypatch.setattr(settings, "axone_settle_minutes", 15)
    src = AxoneLogExportSource(client_factory=lambda: None, clock=lambda: utc(2026, 10, 19, 14, 40))
    windows = src.list_available(utc(2026, 10, 19, 13), utc(2026, 10, 19, 14, 40), {})
    assert [(w.window_start, w.window_end + timedelta(milliseconds=1)) for w in windows] == [
        (utc(2026, 10, 19, 13), utc(2026, 10, 19, 14)),
        (utc(2026, 10, 19, 14), utc(2026, 10, 19, 14, 25)),
    ]


def test_device_windows_are_up_to_7_days_with_exact_edges():
    src = AxoneLogExportSource(client_factory=lambda: None, clock=LATER)
    since = utc(2026, 9, 25, 9, 45)
    windows = src.list_available(since, since + timedelta(days=9), {"device_uuid": DEV})
    assert [w.window_end - w.window_start + timedelta(milliseconds=1) for w in windows] == [
        timedelta(days=7), timedelta(days=2)]
    assert windows[0].window_start == since                       # not rounded to the hour
    assert windows[-1].window_end == since + timedelta(days=9) - timedelta(milliseconds=1)
    assert windows[0].scope == f"dev{DEV}"


def test_bad_category_option():
    src = AxoneLogExportSource(client_factory=lambda: None)
    with pytest.raises(SourceError, match="must be a number"):
        src.list_available(BASE, BASE + timedelta(hours=1), {"event_category": "Security"})


@pytest.mark.parametrize("options,path", [
    ({}, "/getAllDeviceLogsInCSV"),
    ({"event_category": "3"}, "/getLogsByDateAndEventInCsv"),
    ({"device_uuid": DEV}, "/getSpecificDeviceLogsInCSV"),
    ({"device_uuid": DEV, "event_category": "3"}, "/getLogsByDeviceAndEventInCsv"),
])
def test_options_pick_the_right_endpoint(source, fake, tmp_path, options, path):
    remote = source.list_available(BASE, BASE + timedelta(hours=1), options)[0]
    source.fetch(remote, tmp_path)
    assert fake.calls()[-1].url.path == path
    assert source.sleeps == [0.25]  # paused after the call


# --- the full pull through acquire() --------------------------------------------------------

def test_pull_three_hours_then_nothing_new(db, staging, source, fake):
    first = acquire(db, source, actor="tester", since=BASE, until=BASE + timedelta(hours=3), ingest=True)
    assert [f.status for f in first.files] == ["ingested"] * 3
    assert [f.events for f in first.files] == [2, 0, 3]
    assert len(fake.calls("/getAllDeviceLogsInCSV")) == 3
    assert db.query(NormalizedEvent).count() == 5
    rec = db.query(AcquiredFileRecord).order_by(AcquiredFileRecord.id).first()
    assert rec.scope == "alldevices" and rec.window_start is not None

    second = acquire(db, source, actor="tester", since=BASE, until=BASE + timedelta(hours=3), ingest=True)
    assert second.new_files == 0 and second.count("already_acquired") == 3
    assert len(fake.calls("/getAllDeviceLogsInCSV")) == 3   # no new requests
    assert db.query(NormalizedEvent).count() == 5           # nothing doubled


def test_a_failed_hour_is_kept_out_and_retried(db, staging, source, fake):
    fake.fail_hours.add(naive(BASE + timedelta(hours=2)))
    first = acquire(db, source, actor="tester", since=BASE, until=BASE + timedelta(hours=3), ingest=True)
    assert [f.status for f in first.files] == ["ingested", "ingested", "failed"]
    assert "internal error" in first.files[2].error
    assert db.query(NormalizedEvent).count() == 2

    fake.fail_hours.clear()
    second = acquire(db, source, actor="tester", since=BASE, until=BASE + timedelta(hours=3), ingest=True)
    assert second.new_files == 1 and second.count("already_acquired") == 2
    assert db.query(NormalizedEvent).count() == 5


def test_api_rows_parse_into_events(source, tmp_path):
    remote = source.list_available(BASE, BASE + timedelta(hours=1), {})[0]
    events = list(portrait_csv.parse(source.fetch(remote, tmp_path)))
    assert [e.severity for e in events] == ["INFO", "ERROR"]
    assert events[0].process == "axone-log-export"     # ":4521:main" stripped
    assert events[0].device_id == "Hub-01"
    assert events[0].timestamp == BASE + timedelta(minutes=5)


# --- SCRUM-44: exact windows end to end -------------------------------------------------

def _rows_in_db(db):
    return sorted(naive(r.timestamp.replace(tzinfo=timezone.utc)) for r in db.query(NormalizedEvent).all())


def test_only_rows_inside_the_window_are_stored(db, staging, source, fake):
    # Fake rows: BASE+5m, BASE+50m, BASE+2h1m, BASE+2h2m, BASE+2h59m59s.
    since, until = BASE + timedelta(minutes=30), BASE + timedelta(hours=2, minutes=2)
    r = acquire(db, source, actor="tester", since=since, until=until, ingest=True)
    assert [f.status for f in r.files] == ["ingested"] * 3      # 00:30-01:00, 01:00-02:00, 02:00-02:02
    assert _rows_in_db(db) == [naive(BASE + timedelta(minutes=50)), naive(BASE + timedelta(hours=2, minutes=1))]
    for row in _rows_in_db(db):
        assert naive(since) <= row < naive(until)


def test_overlapping_pull_only_requests_the_new_part(db, staging, source, fake):
    acquire(db, source, actor="tester", since=BASE, until=BASE + timedelta(hours=1), ingest=True)
    calls_before = len(fake.calls())
    r = acquire(db, source, actor="tester", since=BASE + timedelta(minutes=30),
                until=BASE + timedelta(hours=2, minutes=30), ingest=True)
    asked = [(c.url.params["startTimestamp"], c.url.params["endTimestamp"]) for c in fake.calls()[calls_before:]]
    fmt = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    # BASE+0:30 to +1:00 was already pulled, so only the new 1h30 is requested.
    assert asked == [
        (fmt(BASE + timedelta(hours=1)), fmt(BASE + timedelta(hours=2) - timedelta(milliseconds=1))),
        (fmt(BASE + timedelta(hours=2)), fmt(BASE + timedelta(hours=2, minutes=30) - timedelta(milliseconds=1))),
    ]
    assert r.count("already_acquired") == 1
    # No row stored twice.
    assert len(_rows_in_db(db)) == len(set(_rows_in_db(db)))


def test_same_window_twice_requests_nothing(db, staging, source, fake):
    w = dict(since=BASE + timedelta(minutes=10), until=BASE + timedelta(minutes=40))
    acquire(db, source, actor="tester", ingest=True, **w)
    n = len(fake.calls())
    r = acquire(db, source, actor="tester", ingest=True, **w)
    assert len(fake.calls()) == n and r.new_files == 0 and r.count("already_acquired") == 1


def test_old_hour_window_rows_count_as_pulled(db, staging, source, fake):
    # A manifest row written before SCRUM-44 (whole-hour window, old id format).
    db.add(AcquiredFileRecord(source="axone", remote_id=f"alldevices|{BASE.isoformat()}", name="old.csv",
                              scope="alldevices", window_start=BASE,
                              window_end=BASE + timedelta(hours=1) - timedelta(milliseconds=1),
                              status="ingested"))
    db.commit()
    r = acquire(db, source, actor="tester", since=BASE + timedelta(minutes=15),
                until=BASE + timedelta(minutes=45), ingest=True)
    assert fake.calls() == [] and r.count("already_acquired") == 1


def test_failed_piece_is_retried_without_refetching_neighbours(db, staging, source, fake):
    fake.fail_hours.add(naive(BASE + timedelta(hours=1)))
    acquire(db, source, actor="tester", since=BASE, until=BASE + timedelta(hours=3), ingest=True)
    fake.fail_hours.clear()
    n = len(fake.calls())
    r = acquire(db, source, actor="tester", since=BASE, until=BASE + timedelta(hours=3), ingest=True)
    retried = fake.calls()[n:]
    assert len(retried) == 1
    assert retried[0].url.params["startTimestamp"][11:13] == (BASE + timedelta(hours=1)).strftime("%H")
    assert r.count("already_acquired") == 2


def test_end_moved_back_for_late_logs_is_reported(db, staging, fake, axone_settings, monkeypatch):
    monkeypatch.setattr(settings, "axone_settle_minutes", 15)
    now = BASE + timedelta(hours=2, minutes=40)
    src = AxoneLogExportSource(
        client_factory=lambda: AxoneLogExportClient("http://fake-axone:9200", transport=fake.transport()),
        sleep=lambda s: None, clock=lambda: now)
    r = acquire(db, src, actor="tester", since=BASE + timedelta(hours=2), until=now, now=now, ingest=True)
    assert r.until == now - timedelta(minutes=15)
    assert len(r.notes) == 1 and "late-arriving logs" in r.notes[0]

    from app.sources.service import WindowError
    with pytest.raises(WindowError, match="Nothing to pull yet"):
        acquire(db, src, actor="tester", since=now - timedelta(minutes=10), until=now, now=now)


# --- review fixes -------------------------------------------------------------------------

def test_quiet_windows_count_as_pulled_even_with_identical_bytes(db, staging, source, fake):
    # Every quiet window is the same header-only CSV. The second one used to be
    # recorded as a "duplicate" file and re-fetched by later overlapping pulls.
    quiet = BASE + timedelta(hours=1)            # the fake has no rows in this hour
    acquire(db, source, actor="tester", since=quiet, until=quiet + timedelta(minutes=30), ingest=True)
    second = acquire(db, source, actor="tester", since=quiet + timedelta(minutes=30),
                     until=quiet + timedelta(hours=1), ingest=True)
    assert [f.status for f in second.files] == ["ingested"]     # not "duplicate"
    n = len(fake.calls())
    third = acquire(db, source, actor="tester", since=quiet + timedelta(minutes=15),
                    until=quiet + timedelta(minutes=45), ingest=True)
    assert len(fake.calls()) == n and third.count("already_acquired") == 1


def test_identical_files_are_still_duplicates_for_file_sources(db, staging, tmp_path):
    # The content check still applies to files: a renamed copy is not re-ingested.
    from app.sources.local_import import LocalImportSource
    (tmp_path / "a.csv").write_text(PORTRAIT_CSV)
    acquire(db, LocalImportSource(), actor="tester", options={"path": str(tmp_path / "a.csv")})
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "a.csv").write_text(PORTRAIT_CSV)
    r = acquire(db, LocalImportSource(), actor="tester", options={"path": str(tmp_path / "b")})
    assert [f.status for f in r.files] == ["already_acquired"]


def test_hours_are_cut_on_the_api_clock_for_half_hour_zones(monkeypatch):
    # +05:30: the API's hours start at :30 UTC, so cuts must fall there.
    monkeypatch.setattr(settings, "axone_timezone", "Asia/Kolkata")
    src = AxoneLogExportSource(client_factory=lambda: None, clock=LATER)
    windows = src.list_available(utc(2026, 10, 19, 9), utc(2026, 10, 19, 11), {})
    assert [(w.window_start, w.window_end + timedelta(milliseconds=1)) for w in windows] == [
        (utc(2026, 10, 19, 9), utc(2026, 10, 19, 9, 30)),
        (utc(2026, 10, 19, 9, 30), utc(2026, 10, 19, 10, 30)),
        (utc(2026, 10, 19, 10, 30), utc(2026, 10, 19, 11)),
    ]
    # Every piece passes the API's same-hour rule on its own clock.
    client = AxoneLogExportClient("http://x:9200", tz="Asia/Kolkata")
    for w in windows:
        client._date_params(w.window_start, w.window_end)


def test_daylight_saving_change_never_makes_a_2_hour_piece(monkeypatch):
    # US fall-back 2026-11-01: 01:00-02:00 local happens twice (06:00 and 07:00 UTC).
    monkeypatch.setattr(settings, "axone_timezone", "America/Chicago")
    src = AxoneLogExportSource(client_factory=lambda: None, clock=LATER)
    windows = src.list_available(utc(2026, 11, 1, 5), utc(2026, 11, 1, 9), {})
    assert all(w.window_end + timedelta(milliseconds=1) - w.window_start == timedelta(hours=1) for w in windows)
    assert len(windows) == 4
