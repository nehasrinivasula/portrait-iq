"""SCRUM-70: device registry (harvested + manual) and pulling one, several or
all devices from the log export API. Fake data only; no network."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.auth import User, get_current_user
from app.config import settings
from app.db import get_db
from app.main import app
from app.models import AuditLogEntry, KnownDevice, NormalizedEvent
from app.sources.axone_client import AxoneLogExportClient
from app.sources.axone_export import MAX_DEVICES_PER_PULL, AxoneLogExportSource
from app.sources.base import SourceError
from app.sources.devices import add_manual, harvest_csv, list_known, record_devices, validate_device_id
from app.sources.local_import import LocalImportSource
from app.sources.service import acquire
from tests.axone_fake import FakeAxone, Row

HDR = ("timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,sourceUUID,"
       "friendlyName,processInfo,sourceInfo,eventCategory,description\n")
HUB = "d4e5f6a7-1234-5678-9abc-def012345678"
VIEWER = "a1b2c3d4-aaaa-bbbb-cccc-123456789abc"
SERVER = "4b5d0104ab"   # not UUID-shaped: the format is not guaranteed, so IDs are not assumed to be UUIDs


def line(ts, host, uuid, name="unsetFriendlyName"):
    return f'"{ts}","0","1","INFO","{host}","{uuid}","{name}","p[1:0]",":0","General","msg"\n'


# --- harvesting ---------------------------------------------------------------------

def test_harvest_reads_ids_names_and_time_range(tmp_path):
    f = tmp_path / "logs.csv"
    f.write_text(HDR
                 + line("2026-10-19T09:00:00.000+0000", "hub-host", HUB, "Hub-01")
                 + line("2026-10-19T11:00:00.000+0000", "hub-host", HUB, "Hub-01-Ward4")   # renamed later
                 + line("2026-10-19T10:00:00.000+0000", "hub-host", HUB, "Hub-01")
                 + line("2026-10-19T09:30:00.000-05:00", "um-pm-0.novalocal", SERVER)      # no friendly name
                 + line("not-a-time", "x", VIEWER)                                         # skipped
                 + line("2026-10-19T09:00:00.000+0000", "x", "bad id!"))                   # skipped
    found = harvest_csv(f)
    assert set(found) == {HUB, SERVER}
    hub = found[HUB]
    assert hub.friendly_name == "Hub-01-Ward4"                    # name at the latest line wins
    assert hub.first_seen == datetime(2026, 10, 19, 9, tzinfo=timezone.utc)
    assert hub.last_seen == datetime(2026, 10, 19, 11, tzinfo=timezone.utc)
    assert found[SERVER].friendly_name is None and found[SERVER].host_name == "um-pm-0.novalocal"
    assert found[SERVER].first_seen == datetime(2026, 10, 19, 14, 30, tzinfo=timezone.utc)   # offset -> UTC


def test_harvest_ignores_files_without_device_ids(tmp_path):
    (tmp_path / "pod.log").write_text("2026-10-19T09:00:00.000Z hello\n")
    (tmp_path / "other.csv").write_text("a,b\n1,2\n")
    assert harvest_csv(tmp_path / "pod.log") == {}
    assert harvest_csv(tmp_path / "other.csv") == {}


def test_record_merges_and_newest_name_wins(db, tmp_path):
    new = tmp_path / "new.csv"
    new.write_text(HDR + line("2026-10-19T12:00:00.000+0000", "h", HUB, "Hub-01-Ward4"))
    old = tmp_path / "old.csv"
    old.write_text(HDR + line("2026-10-18T08:00:00.000+0000", "h", HUB, "Hub-01"))
    assert record_devices(db, harvest_csv(new), "axone") == 1
    assert record_devices(db, harvest_csv(old), "local") == 0     # already known
    d = db.get(KnownDevice, HUB)
    assert d.friendly_name == "Hub-01-Ward4"                       # older file did not overwrite it
    assert d.first_seen == datetime(2026, 10, 18, 8) and d.last_seen == datetime(2026, 10, 19, 12)
    assert d.learned_from == "axone"


# --- manual add -----------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "ab", "has space", "x" * 65, "semi;colon", "../etc"])
def test_device_id_validation(bad):
    with pytest.raises(SourceError, match="not a valid device ID"):
        validate_device_id(bad)


def test_manual_add_then_real_logs_take_over(db, tmp_path):
    d, created = add_manual(db, f"  {VIEWER} ", "Viewer at nurse station")
    assert created and d.device_uuid == VIEWER and d.learned_from == "manual"
    again, created = add_manual(db, VIEWER, "something else")
    assert not created and again.friendly_name == "Viewer at nurse station"   # existing entry kept
    f = tmp_path / "v.csv"
    f.write_text(HDR + line("2026-10-19T09:00:00.000+0000", "viewer-host", VIEWER, "Viewer-01"))
    record_devices(db, harvest_csv(f), "axone")
    d = db.get(KnownDevice, VIEWER)
    assert d.friendly_name == "Viewer-01" and d.learned_from == "axone" and d.last_seen is not None


def test_list_is_sorted_by_display_name(db):
    add_manual(db, SERVER)
    add_manual(db, HUB, "Hub-01")
    add_manual(db, VIEWER, "alpha viewer")
    assert [d.device_uuid for d in list_known(db)] == [SERVER, VIEWER, HUB]   # "4b5d...", "alpha...", "Hub-01"


# --- registry fills itself during acquisition --------------------------------------------

def test_acquiring_logs_records_their_devices(db, staging, tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "logs.csv").write_text(
        HDR + line("2026-10-19T09:00:00.000+0000", "hub-host", HUB, "Hub-01")
        + line("2026-10-19T09:01:00.000+0000", "um-pm-0", SERVER))
    acquire(db, LocalImportSource(), actor="tester", options={"path": str(tmp_path / "in")}, ingest=True)
    assert {d.device_uuid: d.learned_from for d in list_known(db)} == {HUB: "local", SERVER: "local"}


# --- planning a multi-device pull ---------------------------------------------------------

LATER = lambda: datetime(2026, 12, 1, tzinfo=timezone.utc)


def test_several_devices_get_their_own_windows():
    src = AxoneLogExportSource(client_factory=lambda: None, clock=LATER)
    since = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    windows = src.list_available(since, since + timedelta(days=9), {"device_uuids": f"{HUB},{VIEWER},{HUB}"})
    assert [w.scope for w in windows] == [f"dev{HUB}", f"dev{HUB}", f"dev{VIEWER}", f"dev{VIEWER}"]  # deduped
    assert all(w.window_start >= since for w in windows)


def test_no_devices_means_all_devices():
    src = AxoneLogExportSource(client_factory=lambda: None, clock=LATER)
    since = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    windows = src.list_available(since, since + timedelta(hours=2), {"device_uuids": ""})
    assert [w.scope for w in windows] == ["alldevices", "alldevices"]


def test_device_selection_limits():
    src = AxoneLogExportSource(client_factory=lambda: None, clock=LATER)
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    too_many = ",".join(f"dev-{i:04d}" for i in range(MAX_DEVICES_PER_PULL + 1))
    with pytest.raises(SourceError, match="at most 50"):
        src.list_available(since, since + timedelta(hours=1), {"device_uuids": too_many})
    with pytest.raises(SourceError, match="not a valid device ID"):
        src.list_available(since, since + timedelta(hours=1), {"device_uuids": f"{HUB},not valid"})


# --- end to end: pull two devices, then add a third ---------------------------------------

NOW_HOUR = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
BASE = NOW_HOUR - timedelta(hours=6)


@pytest.fixture
def fake():
    def at(minutes):
        return (BASE + timedelta(minutes=minutes)).replace(tzinfo=None)
    return FakeAxone(rows=[Row(at(10), device=HUB), Row(at(70), device=HUB),
                           Row(at(20), device=VIEWER), Row(at(30), device=SERVER)])


@pytest.fixture
def source(fake, monkeypatch):
    monkeypatch.setattr(settings, "axone_base_url", "http://fake-axone:9200")
    monkeypatch.setattr(settings, "axone_timezone", "UTC")
    return AxoneLogExportSource(
        client_factory=lambda: AxoneLogExportClient("http://fake-axone:9200", transport=fake.transport()),
        sleep=lambda s: None)


def test_pull_selected_devices_only(db, staging, source, fake):
    window = dict(since=BASE, until=BASE + timedelta(hours=3))
    r = acquire(db, source, actor="tester", options={"device_uuids": f"{HUB},{VIEWER}"}, ingest=True, **window)
    assert [c.url.path for c in fake.calls()] == ["/getSpecificDeviceLogsInCSV"] * 2
    assert sorted(c.url.params["deviceuuid"] for c in fake.calls()) == sorted([HUB, VIEWER])
    assert r.new_files == 2 and db.query(NormalizedEvent).count() == 3   # 2 hub rows + 1 viewer row

    # Same selection again: nothing new. Add a third device: only it is requested.
    n = len(fake.calls())
    r2 = acquire(db, source, actor="tester", options={"device_uuids": f"{HUB},{VIEWER},{SERVER}"},
                 ingest=True, **window)
    assert [c.url.params["deviceuuid"] for c in fake.calls()[n:]] == [SERVER]
    assert r2.count("already_acquired") == 2 and db.query(NormalizedEvent).count() == 4


# --- the endpoints ---------------------------------------------------------------------------

@pytest.fixture
def client(engine, staging):
    Session = sessionmaker(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: User(id="tester", name="Tester")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_device_endpoints(client, engine):
    assert client.get("/api/sources/devices").json() == []
    r = client.post("/api/sources/devices", json={"device_uuid": HUB, "name": "Hub-01"})
    assert r.status_code == 200 and r.json()["name"] == "Hub-01" and r.json()["learned_from"] == "manual"
    r = client.post("/api/sources/devices", json={"device_uuid": "bad id"})
    assert r.status_code == 400 and "not a valid device ID" in r.json()["detail"]
    assert [d["device_uuid"] for d in client.get("/api/sources/devices").json()] == [HUB]
    s = sessionmaker(bind=engine)()
    assert s.query(AuditLogEntry).filter_by(action="device_add").count() == 1
    s.close()


def test_acquire_request_takes_a_device_list(client, monkeypatch):
    # The device list in the request reaches the source and is validated
    # before any API call is made (planning needs no network).
    monkeypatch.setattr(settings, "axone_base_url", "http://fake-axone:9200")
    r = client.post("/api/sources/axone/acquire", json={"device_uuids": [HUB, "bad id"]})
    assert r.status_code == 400 and "'bad id' is not a valid device ID" in r.json()["detail"]
    r = client.get("/api/sources/axone/files", params={"device_uuids": f"{HUB},{VIEWER}",
                                                       "since": (NOW_HOUR - timedelta(hours=3)).isoformat(),
                                                       "until": (NOW_HOUR - timedelta(hours=2)).isoformat()})
    assert r.status_code == 200 and len(r.json()) == 2   # one window per device


# --- review fixes: scopes, no duplicate lines, learn only from good files ------------------

def test_scope_ids_are_unambiguous_when_device_ids_contain_cat():
    src = AxoneLogExportSource(client_factory=lambda: None, clock=LATER)
    since = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    w = src.list_available(since, since + timedelta(hours=1), {"device_uuids": "hub-cat5", "event_category": "3"})
    assert w[0].scope == "devhub-cat5:cat3"
    assert src._scope_parts("devhub-cat5:cat3") == ("hub-cat5", 3)
    assert src._scope_parts("devhub-cat5") == ("hub-cat5", None)


def test_covering_scopes():
    src = AxoneLogExportSource(client_factory=lambda: None)
    assert src.covering_scopes("alldevices") == ["alldevices"]
    assert src.covering_scopes(f"dev{HUB}") == [f"dev{HUB}", "alldevices"]
    assert src.covering_scopes("cat3") == ["cat3", "alldevices"]
    assert src.covering_scopes(f"dev{HUB}:cat3") == [f"dev{HUB}:cat3", f"dev{HUB}", "cat3", "alldevices"]


def test_all_devices_then_one_device_fetches_nothing(db, staging, source, fake):
    window = dict(since=BASE, until=BASE + timedelta(hours=2))
    acquire(db, source, actor="tester", ingest=True, **window)
    n = len(fake.calls())
    r = acquire(db, source, actor="tester", options={"device_uuids": HUB}, ingest=True, **window)
    assert len(fake.calls()) == n                       # the all-devices pull already has HUB's lines
    assert r.count("already_acquired") == 1 and db.query(NormalizedEvent).count() == 4   # nothing doubled


def test_one_device_then_all_devices_stores_each_line_once(db, staging, source, fake):
    window = dict(since=BASE, until=BASE + timedelta(hours=2))
    acquire(db, source, actor="tester", options={"device_uuids": HUB}, ingest=True, **window)
    assert db.query(NormalizedEvent).count() == 2       # HUB's 2 lines
    r = acquire(db, source, actor="tester", ingest=True, **window)
    # The all-devices files contain HUB's lines too; the earlier copies are replaced.
    assert db.query(NormalizedEvent).count() == 4       # 2 HUB + 1 VIEWER + 1 SERVER, no doubles
    # One HUB line in each hour piece was replaced, and the user is told.
    assert len([n for n in r.notes if "replaced 1 rows" in n]) == 2


def test_a_different_device_is_never_replaced(db, staging, source, fake):
    window = dict(since=BASE, until=BASE + timedelta(hours=2))
    acquire(db, source, actor="tester", options={"device_uuids": VIEWER}, ingest=True, **window)
    acquire(db, source, actor="tester", options={"device_uuids": HUB}, ingest=True, **window)
    assert db.query(NormalizedEvent).count() == 3       # VIEWER's line kept, HUB's 2 added


def test_failed_file_does_not_add_devices(db, staging, tmp_path):
    bad = tmp_path / "bad.csv"   # 1 good row, 5 bad timestamps: over the 1% reject limit, ingestion fails
    bad.write_text(HDR + line("2026-10-19T09:00:00.000+0000", "h", HUB, "Hub-01")
                   + "".join(line("garbage", "h", VIEWER) for _ in range(5)))
    r = acquire(db, LocalImportSource(), actor="tester", options={"path": str(bad)}, ingest=True)
    assert r.files[0].status == "failed" and list_known(db) == []


def test_acquire_without_ingest_still_learns_devices(db, staging, tmp_path):
    f = tmp_path / "ok.csv"
    f.write_text(HDR + line("2026-10-19T09:00:00.000+0000", "h", HUB, "Hub-01"))
    acquire(db, LocalImportSource(), actor="tester", options={"path": str(f)}, ingest=False)
    assert [d.device_uuid for d in list_known(db)] == [HUB]
