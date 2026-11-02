"""
Device discovery (D-3). The cluster is faked; the ARP table and tshark
conversation tables below are synthetic, in the formats the bench prints,
with documentation addresses (192.0.2.0/24).
"""
import pytest
from fastapi import BackgroundTasks, HTTPException  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  (registers tables on Base)
from app.auth import User
from app.collectors import k8s
from app.config import settings
from app.db import Base
from app.discovery import service as discovery
from app.discovery.service import classify, parse_arp, parse_conversations, rtps_domain
from app.models import Device
from app.orchestrator import routes
from app.schemas import DeviceUpdate, DiscoverRequest

NODE = "192.0.2.3"

ARP = """\
IP address       HW type     Flags       HW address            Mask     Device
192.0.2.10       0x1         0x2         02:00:00:00:00:10     *        eth0
192.0.2.5        0x1         0x2         02:00:00:00:00:05     *        eth0
192.0.2.4        0x1         0x2         02:00:00:00:00:04     *        eth0
192.0.2.99       0x1         0x0         00:00:00:00:00:00     *        eth0
100.64.64.236    0x1         0x2         02:00:00:00:00:aa     *        cni0
10.42.0.7        0x1         0x2         02:00:00:00:00:bb     *        eth0
"""

CONV = """\
================================================================================
UDP Conversations
Filter:<No Filter>
                                               |       <-      | |       ->      | |     Total     |
                                               | Frames  Bytes | | Frames  Bytes | | Frames  Bytes |
192.0.2.10:10535        <-> 192.0.2.3:32013         6 6744 bytes     410 292 kB        416 299 kB
192.0.2.10:9160         <-> 192.0.2.3:32007         2 900 bytes       40 20 kB          42 21 kB
192.0.2.3:32014         <-> 192.0.2.5:10700         3 1000 bytes      90 50 kB          93 51 kB
100.64.64.244:35983     <-> 100.64.64.236:9174      0 0 bytes        377 67 kB         377 67 kB
================================================================================
TCP Conversations
Filter:<No Filter>
192.0.2.4:51000         <-> 192.0.2.3:443           5 2 kB            5 3 kB            10 5 kB
================================================================================
"""


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class FakeRunner:
    def __init__(self, node_ip=NODE, arp=ARP, conv=CONV, fail_capture=False):
        self.node_ip, self.arp, self.conv, self.fail_capture = node_ip, arp, conv, fail_capture
        self.created, self.execs, self.deleted = [], [], []

    def create(self, **kw):
        self.created.append(kw)

    def wait_running(self, name, timeout_s):
        return self.node_ip

    def exec(self, name, argv, timeout_s):
        self.execs.append(argv)
        if argv[0] == "cat":
            return k8s.ExecResult(0, self.arp, "")
        if "-w" in argv:
            return k8s.ExecResult(1 if self.fail_capture else 0, "", "")
        return k8s.ExecResult(0, self.conv, "")

    def delete(self, name):
        self.deleted.append(name)


@pytest.fixture
def runner(monkeypatch):
    r = FakeRunner()
    monkeypatch.setattr(k8s, "get_pod_runner", lambda: r)
    monkeypatch.setattr(settings, "tshark_live_capture_enabled", True)
    return r


def test_rtps_domain():
    assert [rtps_domain(p) for p in (9150, 9399, 10400, 10649, 10650, 10899, 7400, 32013)] == \
        [7, 7, 12, 12, 13, 13, None, None]


def test_parse_arp_keeps_only_complete_site_entries():
    assert parse_arp(ARP) == {
        "192.0.2.10": "02:00:00:00:00:10", "192.0.2.5": "02:00:00:00:00:05", "192.0.2.4": "02:00:00:00:00:04",
    }


def test_parse_conversations_keeps_peers_of_the_node():
    peers = parse_conversations(CONV, NODE)
    assert set(peers) == {"192.0.2.10", "192.0.2.5", "192.0.2.4"}   # pod-to-pod traffic left out
    assert peers["192.0.2.10"].udp == {10535, 9160}
    assert peers["192.0.2.10"].to_node == {"udp 32013", "udp 32007"}
    assert peers["192.0.2.5"].udp == {10700}                          # node listed first
    assert peers["192.0.2.4"].to_node == {"tcp 443"}


def test_classify_by_rti_domain():
    peers = parse_conversations(CONV, NODE)
    hub = classify("192.0.2.10", peers["192.0.2.10"], None)
    assert hub.kind == "hub"
    assert hub.evidence[:2] == ["RTI domain 7 (logging)", "RTI domain 12 (source pod)"]
    assert classify("192.0.2.5", peers["192.0.2.5"], None).kind == "viewer"
    other = classify("192.0.2.4", peers["192.0.2.4"], "02:00:00:00:00:04")
    assert other.kind == "unknown"
    assert other.evidence == ["talks to the system on tcp 443", "in the system's ARP table"]


def test_discovery_finds_hub_viewer_and_others_and_cleans_up(runner):
    result = discovery.run_discovery(20)
    assert [(d.ip, d.kind) for d in result.devices] == [
        (NODE, "server"), ("192.0.2.4", "unknown"), ("192.0.2.5", "viewer"), ("192.0.2.10", "hub"),
    ]
    pod = runner.created[0]
    assert pod["host_network"] is True and pod["image"] == settings.tshark_image
    assert pod["add_capabilities"] == ("NET_RAW", "NET_ADMIN")
    capture = next(a for a in runner.execs if "-w" in a)
    assert capture[capture.index("-f") + 1] == f"host {NODE}"
    assert capture[capture.index("-s") + 1] == "128"            # headers only
    assert "duration:20" in capture
    assert not any(a[0] in ("cp", "base64") for a in runner.execs)   # the capture never leaves the pod
    assert runner.deleted == [pod["name"]]
    assert result.captured and result.notes == []


def test_without_live_capture_only_the_arp_table_is_read(runner, monkeypatch):
    monkeypatch.setattr(settings, "tshark_live_capture_enabled", False)
    result = discovery.run_discovery(20)
    assert runner.created[0]["add_capabilities"] == ()
    assert [a[0] for a in runner.execs] == ["cat"]
    assert {d.kind for d in result.devices} == {"server", "unknown"}
    assert not result.captured and "ARP table only" in result.notes[0]


def test_failed_capture_still_deletes_the_pod(runner):
    runner.fail_capture = True
    with pytest.raises(discovery.PluginError, match="capture"):
        discovery.run_discovery(20)
    assert runner.deleted == [runner.created[0]["name"]]


def test_one_search_at_a_time(runner):
    assert discovery._lock.acquire(blocking=False)
    try:
        with pytest.raises(discovery.DiscoveryBusy):
            discovery.run_discovery(20)
    finally:
        discovery._lock.release()
    assert runner.created == []


def test_save_keeps_engineer_overrides_and_marks_missing_devices(db, runner):
    discovery.save(db, discovery.run_discovery(20))
    hub = db.query(Device).filter_by(ip_address="192.0.2.10").one()
    assert (hub.name, hub.kind, hub.reachable) == ("Hub", "hub", 1)

    user = User(id="tester", name="Tester")
    routes.update_device(hub.id, DeviceUpdate(name="Ward 4 hub"), db=db, user=user)
    other = db.query(Device).filter_by(ip_address="192.0.2.4").one()
    routes.update_device(other.id, DeviceUpdate(kind="viewer"), db=db, user=user)
    assert other.name == "Viewer"             # the generated name follows the role

    runner.conv = (CONV.replace("192.0.2.10:10535", "192.0.2.10:10900")   # hub now looks unknown
                   .replace("192.0.2.5:10700", "192.0.2.77:10700"))      # viewer gone
    runner.arp = ARP.replace("192.0.2.5 ", "192.0.2.77")
    discovery.save(db, discovery.run_discovery(20))
    db.refresh(hub)
    assert (hub.name, hub.kind) == ("Ward 4 hub", "hub")                 # override kept
    viewer = db.query(Device).filter_by(ip_address="192.0.2.5").one()
    assert viewer.reachable == 0


def test_discover_route_returns_evidence_and_audits(db, runner):
    user = User(id="tester", name="Tester")
    out = routes.discover(DiscoverRequest(duration_s=20), db=db, user=user)
    assert out.system_ip == NODE and out.traffic_checked
    hub = next(d for d in out.devices if d.kind == "hub")
    assert "RTI domain 12 (source pod)" in hub.evidence
    details = [e.detail for e in db.query(models.AuditLogEntry).filter_by(action="discover")]
    assert details[0] == "duration_s=20" and "found=4" in details[1]


def test_discover_route_errors(db, runner):
    user = User(id="tester", name="Tester")
    runner.fail_capture = True
    with pytest.raises(HTTPException) as e:
        routes.discover(DiscoverRequest(), db=db, user=user)
    assert e.value.status_code == 502
    with pytest.raises(HTTPException) as e:
        routes.update_device("nope", DeviceUpdate(name="x"), db=db, user=user)
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        routes.update_device("nope", DeviceUpdate(), db=db, user=user)
    assert e.value.status_code in (400, 404)
