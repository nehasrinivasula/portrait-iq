"""
SCRUM-39: the tshark plugin and file uploads. tshark/editcap and the
cluster are faked; test_real_tshark_reads_a_synthetic_capture runs the
real tools when they are installed. Captures here are built byte by byte:
never use a real capture in a test.
"""
import io
import shutil
import stat
import struct

import pytest
from fastapi import HTTPException, UploadFile

from app import uploads
from app.auth import User
from app.collectors import k8s
from app.collectors import tshark as tshark_module
from app.collectors.base import PluginConfigError, PluginError, ResourceLimits
from app.collectors.tshark import VISIBILITY_WARNING, TsharkCollector
from app.config import settings
from app.orchestrator import routes

LIMITS = ResourceLimits(cpu_pct=50, mem_mb=512, max_output_mb=100)


def synthetic_pcap(packets: int = 3) -> bytes:
    """Classic pcap with UDP packets between two documentation addresses."""
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)   # Ethernet
    for i in range(packets):
        payload = b"synthetic test payload %d" % i
        udp = struct.pack("!HHHH", 40000, 7400, 8 + len(payload), 0) + payload
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), i, 0, 64, 17, 0,
                         bytes([192, 0, 2, 1]), bytes([192, 0, 2, 2])) + udp
        frame = b"\x02" * 6 + b"\x04" * 6 + b"\x08\x00" + ip
        out += struct.pack("<IIII", 1_700_000_000 + i, 0, len(frame), len(frame)) + frame
    return out


@pytest.fixture
def live_capture(monkeypatch):
    monkeypatch.setattr(settings, "tshark_live_capture_enabled", True)


@pytest.fixture
def tools(monkeypatch):
    """Fake tshark/editcap: records calls, editcap copies its input."""
    calls = []

    def fake(name, args, timeout_s):
        calls.append([name, *args])
        if name == "editcap":
            shutil.copyfile(args[-2], args[-1])
            return ""
        return "eth  frames:3 bytes:180\n  ip  frames:3 bytes:138\n"
    monkeypatch.setattr(tshark_module, "_wireshark_tool", fake)
    return calls


def upload(data: bytes = None) -> str:
    return uploads.save(io.BytesIO(data or synthetic_pcap()), "pcap")[0]


def imported(tmp_path, **options) -> TsharkCollector:
    t = TsharkCollector(LIMITS)
    t.configure({"mode": "import", "upload_id": upload(), **options})
    t.bind_job("job-1", tmp_path)
    return t


# configure ------------------------------------------------------------------

def test_live_capture_is_off_unless_enabled():
    with pytest.raises(PluginConfigError, match="live capture is turned off"):
        TsharkCollector(LIMITS).configure({})


def test_run_options(live_capture):
    t = TsharkCollector(LIMITS)
    t.configure({"duration_s": 30, "interface": "eth0", "capture_filter": "udp port 7400 or host 192.0.2.1"})
    assert (t.duration_s, t.interface, t.capture_filter) == (30, "eth0", "udp port 7400 or host 192.0.2.1")
    for bad in (
        {"duration_s": 4}, {"duration_s": 901}, {"duration_s": "60"}, {"duration_s": True},
        {"interface": "-w"}, {"interface": "eth0; rm"},
        {"capture_filter": "port 80; reboot"}, {"capture_filter": "$(id)"}, {"capture_filter": 7},
        {"upload_id": "0" * 32},   # import-only option in run mode
    ):
        with pytest.raises(PluginConfigError):
            t.configure(bad)


def test_import_needs_an_existing_upload():
    t = TsharkCollector(LIMITS)
    for bad in ({}, {"upload_id": "../../etc/passwd"}, {"upload_id": "0" * 32}):
        with pytest.raises(PluginConfigError, match="upload_id|no uploaded capture"):
            t.configure({"mode": "import", **bad})
    with pytest.raises(PluginConfigError, match="does not take"):
        t.configure({"mode": "import", "upload_id": upload(), "duration_s": 30})
    t.configure({"mode": "import", "upload_id": (uid := upload())})
    assert t.audit_fields() == {"mode": "import", "headers_only": True, "upload_id": uid}


# import mode ---------------------------------------------------------------

def test_import_keeps_headers_only_by_default(tmp_path, tools):
    t = imported(tmp_path)
    result = t.run("192.0.2.10")
    assert tools[0][:5] == ["editcap", "-F", "pcapng", "-s", "128"]
    assert tools[1][:4] == ["tshark", "-n", "-r", str(tmp_path / "capture.pcapng")]
    assert result.summary == "3 packets imported, headers only"
    assert result.warnings == []
    assert (tmp_path / "summary.txt").read_text().startswith("eth  frames:3")
    t.cleanup()   # nothing to tear down


def test_import_full_payload_is_not_cut(tmp_path, tools):
    t = imported(tmp_path, headers_only=False, full_payload_reason="ticket 123")
    t.run("192.0.2.10")
    assert "-s" not in tools[0]


def test_import_respects_the_jobs_output_limit(tmp_path, tools):
    t = TsharkCollector(ResourceLimits(cpu_pct=50, mem_mb=512, max_output_mb=0))
    t.configure({"mode": "import", "upload_id": upload()})
    t.bind_job("job-1", tmp_path)
    with pytest.raises(PluginError, match="over this job's 0 MB limit"):
        t.run("192.0.2.10")


def test_empty_capture_is_a_warning(tmp_path, monkeypatch):
    def no_packets(name, args, timeout_s):
        if name == "editcap":
            shutil.copyfile(args[-2], args[-1])
        return ""
    monkeypatch.setattr(tshark_module, "_wireshark_tool", no_packets)
    result = imported(tmp_path).run("192.0.2.10")
    assert "no packets" in result.warnings[0]


def test_missing_tshark_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(tshark_module.shutil, "which", lambda name, path=None: None)
    with pytest.raises(PluginError, match="tshark is not installed .*apt install tshark"):
        tshark_module._wireshark_tool("tshark", ["-v"], 5)


@pytest.mark.skipif(not (shutil.which("tshark") and shutil.which("editcap")), reason="Wireshark CLI not installed")
def test_real_tshark_reads_a_synthetic_capture(tmp_path):
    result = imported(tmp_path).run("192.0.2.10")
    assert result.summary == "3 packets imported, headers only"
    assert "192.0.2.1" in (tmp_path / "summary.txt").read_text()


# run mode -------------------------------------------------------------------

class FakeRunner:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code
        self.created, self.execs, self.copied, self.deleted = [], [], [], []

    def create(self, **kw):
        self.created.append(kw)

    def wait_running(self, name, timeout_s):
        pass

    def exec(self, name, argv, timeout_s):
        self.execs.append(argv)
        return k8s.ExecResult(self.exit_code, "", "Capturing on 'any'\ntshark: permission denied\n")

    def read_file(self, name, path, dest, max_bytes):
        self.copied.append((path, max_bytes))
        dest.write_bytes(synthetic_pcap())
        return dest.stat().st_size

    def delete(self, name):
        self.deleted.append(name)


def test_live_capture(tmp_path, live_capture, tools, monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(k8s, "get_pod_runner", lambda: runner)
    t = TsharkCollector(LIMITS)
    t.configure({"duration_s": 30, "capture_filter": "udp port 7400"})
    t.bind_job("job-1", tmp_path)
    result = t.run("192.0.2.10")

    pod = runner.created[0]
    assert pod["host_network"] is True and pod["add_capabilities"] == ("NET_RAW", "NET_ADMIN")
    assert pod["image"] == settings.tshark_image
    argv = runner.execs[0]
    assert argv[:3] == ["tshark", "-i", "any"]
    assert "duration:30" in argv and "filesize:102400" in argv
    assert argv[argv.index("-s") + 1] == "128"
    assert argv[argv.index("-f") + 1] == "udp port 7400"
    assert runner.copied == [("/tmp/capture.pcapng", 101 * 1024 * 1024)]
    assert result.summary == "3 packets captured over 30s, headers only"
    assert result.warnings == [VISIBILITY_WARNING]
    t.cleanup()
    assert runner.deleted == [pod["name"]]


def test_failed_capture_reports_tsharks_reason(tmp_path, live_capture, tools, monkeypatch):
    runner = FakeRunner(exit_code=2)
    monkeypatch.setattr(k8s, "get_pod_runner", lambda: runner)
    t = TsharkCollector(LIMITS)
    t.configure({})
    t.bind_job("job-1", tmp_path)
    with pytest.raises(PluginError, match=r"tshark capture failed \(exit 2\) tshark: permission denied"):
        t.run("192.0.2.10")
    t.cleanup()
    assert runner.deleted == [runner.created[0]["name"]]


# uploads --------------------------------------------------------------------

def test_upload_is_stored_owner_only_under_a_random_name():
    upload_id, size = uploads.save(io.BytesIO(synthetic_pcap()), "pcap")
    path = uploads.path_for(upload_id, "pcap")
    assert uploads.is_valid_id(upload_id) and size == path.stat().st_size
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("data,kind,error", [
    (b"PK\x03\x04 a zip, not a capture", "pcap", "not a pcap file"),
    (b"", "pcap", "empty"),
    (b"\x0a\x0d\x0d\x0a", "exe", "unsupported upload kind"),
])
def test_bad_uploads_are_refused_and_leave_nothing(data, kind, error, tmp_path):
    with pytest.raises(uploads.UploadError, match=error):
        uploads.save(io.BytesIO(data), kind)
    folder = tmp_path / "uploads"
    assert not folder.exists() or list(folder.iterdir()) == []


def test_upload_size_limit(monkeypatch):
    monkeypatch.setattr(settings, "upload_max_mb", 1)
    with pytest.raises(uploads.UploadError, match="larger than the 1 MB limit"):
        uploads.save(io.BytesIO(b"\x0a\x0d\x0d\x0a" + b"\0" * (2 * 1024 * 1024)), "pcap")


def test_upload_endpoint_audits_without_the_file_name():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models
    from app.db import Base
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(id="tester", name="Tester")

    file = UploadFile(io.BytesIO(synthetic_pcap()), filename="Jane Doe bed 4.pcap")
    out = routes.upload_file(kind="pcap", file=file, db=db, user=user)
    assert out.kind == "pcap" and uploads.exists(out.upload_id, "pcap")
    detail = db.query(models.AuditLogEntry).filter_by(action="upload").one().detail
    assert out.upload_id in detail and "Jane" not in detail

    with pytest.raises(HTTPException) as exc:
        routes.upload_file(kind="pcap", file=UploadFile(io.BytesIO(b"nope"), filename="x"), db=db, user=user)
    assert exc.value.status_code == 400
