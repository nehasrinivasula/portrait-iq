"""
SCRUM-39: the UMF RTI plugin. The cluster and rtiddsspy are faked; the spy
output below is synthetic (documentation addresses, made-up topics).
"""
import hashlib
import io
import json

import struct

import pytest

from app import uploads
from app.collectors import k8s, rti
from app.collectors.base import PluginConfigError, PluginError, ResourceLimits
from app.collectors.rti import RtiCollector, summarize, summarize_admin_console, summarize_monitor_session
from app.config import settings
from tests.test_javaser import SC_BLOCK_DATA, SC_EXTERNALIZABLE, W

LIMITS = ResourceLimits(cpu_pct=50, mem_mb=512, max_output_mb=100)

# rtiddsspy's real discovery-table format (confirmed on the bench 2026-11-08):
# source_timestamp, Info (W/R + change code), Src HostId (hex), topic, type.
# Host ids and topics here are synthetic. A "New data ..." style line is left
# in to confirm sample lines are not matched.
SPY_OUTPUT = """\
RTI Connext DDS Spy built with DDS version: 6.1.2.23
source_timestamp   Info  Src HostId  topic             type
-----------------  ----  ----------  ----------------  ----------------
1700000000.000001  W +N  0A000215    SyntheticVitals   SyntheticVitalsType
1700000000.000002  R +N  0A00021E    SyntheticVitals   SyntheticVitalsType
1700000000.000005  W +N  0A00021E    SyntheticAlarm    SyntheticAlarmType
1700000000.000006  D     0A000215    SyntheticVitals   SyntheticVitalsType
some line rtiddsspy prints that the parser does not know
"""


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "rti_enabled", True)


@pytest.fixture(autouse=True)
def _no_domain_leaks():
    """RTI holds a per-domain claim from run() until cleanup(); clear it between
    tests so a test that runs without cleaning up does not block later ones."""
    rti._active_domains.clear()
    yield
    rti._active_domains.clear()


class FakeRunner:
    def __init__(self, exit_code=124, stdout=SPY_OUTPUT, stderr=""):
        self.exit_code, self.stdout, self.stderr = exit_code, stdout, stderr
        self.created, self.execs, self.deleted = [], [], []

    def create(self, **kw):
        self.created.append(kw)

    def wait_running(self, name, timeout_s):
        return "192.0.2.5"   # the node IP, used as the WAN public address

    def exec(self, name, argv, timeout_s):
        self.execs.append(argv)
        return k8s.ExecResult(self.exit_code, self.stdout, self.stderr)

    def delete(self, name):
        self.deleted.append(name)


def configured(tmp_path, monkeypatch, runner, **options) -> RtiCollector:
    monkeypatch.setattr(k8s, "get_pod_runner", lambda: runner)
    r = RtiCollector(LIMITS)
    r.configure(options)
    r.bind_job("job-1", tmp_path)
    return r


def test_off_unless_enabled():
    with pytest.raises(PluginConfigError, match="rtitools image is missing"):
        RtiCollector(LIMITS).configure({})


def test_options(enabled):
    r = RtiCollector(LIMITS)
    r.configure({"domain": 7, "duration_s": 60})
    assert r.audit_fields() == {"domain": 7, "duration_s": 60}
    for bad in ({"domain": 99}, {"domain": "12"}, {"domain": True}, {"duration_s": 9},
                {"duration_s": 301}, {"duration_s": "30"}, {"print_sample": True}):
        with pytest.raises(PluginConfigError):
            r.configure(bad)


def test_listens_over_wan_without_printing_samples(tmp_path, monkeypatch, enabled):
    runner = FakeRunner()
    result = configured(tmp_path, monkeypatch, runner, duration_s=20).run("192.0.2.10")

    pod = runner.created[0]
    assert pod["host_network"] is True and pod["image"] == settings.rti_image
    assert "add_capabilities" not in pod
    argv = runner.execs[0]
    # Real-Time WAN: DDS_SECURE + the RTWAN env set via `env`, a udpv4_wan peer
    # at the router's domain-12 NodePort, and the WAN source-pod QoS profile.
    assert argv[0] == "env"
    assert "DDS_SECURE=secure" in argv
    assert "DDS_RTWAN_SERVER_PUBLIC_ADDRESS=192.0.2.5" in argv       # the node IP
    assert "DDS_RTWAN_SERVER_PORT_SOURCEPOD=32113" in argv           # 32013 + offset 100
    assert argv[argv.index("rtiddsspy") - 2:argv.index("rtiddsspy")] == ["timeout", "20"]
    assert argv[argv.index("-domainId") + 1] == "12"
    assert argv[argv.index("-peer") + 1] == "udpv4_wan://192.0.2.10:32013"
    assert argv[argv.index("-qosProfile") + 1] == "AxoneNetQoSLibrary::umfwan_server_sourcepod_qos"
    assert "-printSample" not in argv

    assert result.summary == "2 hosts, 2 topics on domain 12 over 20s"
    assert result.warnings == []
    saved = json.loads((tmp_path / "rti.json").read_text())
    assert saved["topics"]["SyntheticVitals"] == {
        "type": "SyntheticVitalsType", "writers": ["0A000215"], "readers": ["0A00021E"],
    }


def test_wan_profile_port_and_peer_are_per_domain(tmp_path, monkeypatch, enabled):
    for domain, role, nodeport in [(7, "logger", 32007), (13, "viewpod", 32015)]:
        runner = FakeRunner()
        configured(tmp_path, monkeypatch, runner, domain=domain).run("192.0.2.10")
        argv = runner.execs[0]
        assert argv[argv.index("-domainId") + 1] == str(domain)
        assert argv[argv.index("-peer") + 1] == f"udpv4_wan://192.0.2.10:{nodeport}"
        assert argv[argv.index("-qosProfile") + 1] == f"AxoneNetQoSLibrary::umfwan_server_{role}_qos"
        assert f"DDS_RTWAN_SERVER_PORT_{role.upper()}={nodeport + 100}" in argv


def test_every_domain_wan_port_is_defined_so_the_shared_qos_file_parses(tmp_path, monkeypatch, enabled):
    # The -WithWAN QoS file references every domain's DDS_RTWAN_SERVER_PORT_*,
    # and RTI expands them all when it parses the file (before a profile is
    # chosen), so every one must be set even though a single domain is driven.
    # Regression: setting only the selected domain's port made the whole file
    # fail to parse on the bench ("Undefined environment variable
    # DDS_RTWAN_SERVER_PORT_LOGGER").
    runner = FakeRunner()
    configured(tmp_path, monkeypatch, runner, domain=12).run("192.0.2.10")
    argv = runner.execs[0]
    assert "DDS_RTWAN_SERVER_PORT_LOGGER=32107" in argv      # 32007 + offset 100
    assert "DDS_RTWAN_SERVER_PORT_SOURCEPOD=32113" in argv   # 32013 + offset 100
    assert "DDS_RTWAN_SERVER_PORT_VIEWPOD=32115" in argv     # 32015 + offset 100


def test_no_security_secret_is_mounted_by_default(tmp_path, monkeypatch, enabled):
    runner = FakeRunner()
    configured(tmp_path, monkeypatch, runner, duration_s=20).run("192.0.2.10")
    assert runner.created[0].get("secret_mount") is None


def test_security_secret_is_mounted_over_the_sdds_dir(tmp_path, monkeypatch, enabled):
    monkeypatch.setattr(settings, "rti_security_secret", "rti-dds-security")
    monkeypatch.setattr(settings, "rti_sdds_dir", "/opt/axone-tools/sdds")
    runner = FakeRunner()
    configured(tmp_path, monkeypatch, runner, duration_s=20).run("192.0.2.10")
    assert runner.created[0]["secret_mount"] == ("rti-dds-security", "/opt/axone-tools/sdds")


def test_nothing_seen_is_a_warning(tmp_path, monkeypatch, enabled):
    result = configured(tmp_path, monkeypatch, FakeRunner(stdout="")).run("192.0.2.10")
    assert result.summary.startswith("0 hosts, 0 topics")
    assert "No DDS participants seen" in result.warnings[0]


def test_spy_failure_is_a_plugin_error_and_cleanup_deletes_the_pod(tmp_path, monkeypatch, enabled):
    runner = FakeRunner(exit_code=1, stdout="", stderr="ERROR: could not load QoS profile\n")
    r = configured(tmp_path, monkeypatch, runner)
    with pytest.raises(PluginError, match="could not load QoS profile"):
        r.run("192.0.2.10")
    r.cleanup()
    assert runner.deleted == [runner.created[0]["name"]]


def test_bad_target_is_refused_before_any_pod(tmp_path, monkeypatch, enabled):
    runner = FakeRunner()
    with pytest.raises(PluginError, match="not an IP address"):
        configured(tmp_path, monkeypatch, runner).run("-oProxyCommand=x")
    assert runner.created == []


def test_summary_ignores_unknown_lines():
    assert summarize("nothing useful\n")["topics"] == {}
    assert summarize(SPY_OUTPUT)["hosts"] == ["0A000215", "0A00021E"]


def test_configured_peer_replaces_the_target(tmp_path, monkeypatch, enabled):
    monkeypatch.setattr(settings, "rti_peer", "udpv4://router.example.svc.cluster.local")
    runner = FakeRunner()
    configured(tmp_path, monkeypatch, runner).run("192.0.2.10")
    argv = runner.execs[0]
    assert argv[argv.index("-peer") + 1] == "udpv4://router.example.svc.cluster.local"


# SCRUM-39 review, finding 1: one RTI run per domain per node, so two runs do
# not fight for the same local host WAN port.

def test_one_run_per_domain_at_a_time(tmp_path, monkeypatch, enabled):
    first = configured(tmp_path, monkeypatch, FakeRunner(), domain=12)
    first.run("192.0.2.10")                       # claims domain 12, held until cleanup

    blocked_runner = FakeRunner()
    blocked = configured(tmp_path, monkeypatch, blocked_runner, domain=12)
    with pytest.raises(PluginError, match="already running on domain 12"):
        blocked.run("192.0.2.10")
    assert blocked_runner.created == []           # refused before any pod was deployed

    configured(tmp_path, monkeypatch, FakeRunner(), domain=13).run("192.0.2.10")   # other domain is fine

    first.cleanup()                               # releases domain 12
    configured(tmp_path, monkeypatch, FakeRunner(), domain=12).run("192.0.2.10")   # free again, no raise


# SCRUM-39 follow-up review: cleanup() must not release the domain while the pod
# may still be running (and still holding its WAN port). A failed delete keeps
# the domain blocked until a later cleanup confirms the pod is gone.

def test_a_failed_pod_deletion_keeps_the_domain_blocked(tmp_path, monkeypatch, enabled):
    class FlakyDeleteRunner(FakeRunner):
        fail_delete = True

        def delete(self, name):
            if self.fail_delete:
                raise RuntimeError("kubernetes could not delete the pod")
            super().delete(name)

    runner = FlakyDeleteRunner()
    r = configured(tmp_path, monkeypatch, runner, domain=12)
    r.run("192.0.2.10")                               # claims domain 12

    with pytest.raises(RuntimeError, match="could not delete"):
        r.cleanup()                                   # delete fails: claim must survive
    assert runner.deleted == []

    blocked = configured(tmp_path, monkeypatch, FakeRunner(), domain=12)
    with pytest.raises(PluginError, match="already running on domain 12"):
        blocked.run("192.0.2.10")                     # domain still blocked by the stuck pod

    runner.fail_delete = False
    r.cleanup()                                       # pod confirmed gone, claim released
    assert runner.deleted == [runner.created[0]["name"]]
    configured(tmp_path, monkeypatch, FakeRunner(), domain=12).run("192.0.2.10")   # free again, no raise


# SCRUM-39 review, finding 2: a participant deleted mid-capture leaves the summary.

def test_summary_drops_participants_deleted_during_the_capture():
    out = summarize(
        "1.0  W +N  0A000215  umfvitals  umfvitals::T\n"
        "1.1  R +N  0A00021E  umfvitals  umfvitals::T\n"
        "1.2  W -D  0A000215  umfvitals  umfvitals::T\n"   # the writer leaves before the run ends
    )
    assert out["topics"]["umfvitals"]["writers"] == []
    assert out["topics"]["umfvitals"]["readers"] == ["0A00021E"]
    assert out["hosts"] == ["0A00021E"]


def test_summary_drops_topics_that_end_with_no_participants():
    out = summarize(
        "1.0  W +N  0A000215  gone  gone::T\n"
        "1.1  W -D  0A000215  gone  gone::T\n"
    )
    assert out["topics"] == {} and out["hosts"] == []


# SCRUM-123: the GUI route. The operator saves an RTI tool's export and uploads
# it; import mode ingests that file instead of running the spy. The export
# bytes below are synthetic (documentation topic names, not a real system).

def _ep(kind: str, topic: str, rel: str = "RELIABLE_RELIABILITY_QOS") -> str:
    """One publication/subscription in Admin Console's export layout."""
    return (f"<element><{kind}_data><topic_name>{topic}</topic_name>"
            f"<type_name>{topic}::T</type_name><{kind}_name><name>ep</name></{kind}_name>"
            f"<reliability><kind>{rel}</kind></reliability></{kind}_data>"
            f"<sample_info><valid_data>true</valid_data></sample_info></element>")


def _participant(domain: int, name: str, pubs=(), subs=(), monitored=True) -> str:
    props = [("dds.sys_info.process_id", "8"), ("axone_service_friendlyname", "router-x")]
    if monitored:
        props += [("rti/dds/monitor/version", "6.1.2")]
    prop_xml = "".join(f"<element><name>{k}</name><value>{v}</value><propagate>true</propagate></element>"
                       for k, v in props)
    return (f"<element><domain_id>{domain}</domain_id><participant_data>"
            f"<domain_id>{domain}</domain_id><participant_name><name>{name}</name></participant_name>"
            "<product_version><major>6</major><minor>1</minor><release>2</release><revision>31</revision>"
            f"</product_version><property><value>{prop_xml}</value></property></participant_data>"
            f"<publications><value>{''.join(_ep('publication', t) for t in pubs)}</value></publications>"
            f"<subscriptions><value>{''.join(_ep('subscription', t) for t in subs)}</value></subscriptions>"
            "</element>")


# Same layout as a real Admin Console 6.1.2 "Export Discovery Data" file from
# the bench (2026-11-24) -- leading comment, no XML declaration, values as
# element text -- with synthetic names.
ADMIN_XML = (
    "<!-- This is a discovery data file written by Admin Console version 6.1.2.23 -->\n"
    "<system><product_version><major>6</major><minor>1</minor><release>2</release>"
    "<revision>23</revision></product_version>"
    "<serialization_date>2026-11-24 20:17:30.406 UTC</serialization_date>"
    "<processes><value><element><domain_participants><value>"
    + _participant(12, "router#source", pubs=["umfvitals", "umfalarm"], subs=["umfvitals", "umfcmd"])
    + _participant(13, "router#view", pubs=["umfvitals"], subs=[], monitored=False)
    + "</value></domain_participants><log_messages/><security_log_messages/></element></value></processes>"
    "</system>"
).encode()
def _model(w: W, kind: str, entity_id: bytes, status: str, *strings):
    """One RTI Monitor model as Monitor 6.1.2 writes it: Externalizable, an
    ordered list of values -- 17-byte id, MonitoringStatus, then names."""
    w.obj(f"com.rti.dds.monitor.system.{kind}", SC_EXTERNALIZABLE | SC_BLOCK_DATA)
    w.block(entity_id + b"\x00")
    w.enum("com.rti.dds.monitor.system.MonitoringStatus", status)
    w.string(f"rti/dds/monitoring/entityStatus/{kind}/{len(w.b)}")   # property keys are skipped
    for s in strings:
        w.string(s)
    w.end()


def monitor_session(statuses=("MONITORING_SUPPORTED_NO_DATA_AVAILABLE",) * 3) -> bytes:
    """Same layout as a real RTI Monitor 6.1.2.23 saved session from the bench
    (2026-11-24): version string, entity count, then one model per entity.
    Synthetic names."""
    agg = "MONITORING_SUPPORTED_THROUGH_AGGREGATION"
    dom12 = b"\xff" * 4 + struct.pack(">i", 12) + b"\xff" * 4 + b"\x00\x00\x00\x01"
    dom13 = b"\xff" * 4 + struct.pack(">i", 13) + b"\xff" * 4 + b"\x00\x00\x00\x02"
    part, writer, reader = (bytes([i]) * 16 for i in (1, 2, 3))
    w = W()
    w.string("6.1.2.23")
    w.block(struct.pack(">i", 8))
    _model(w, "SystemModel", b"\x00" * 16, agg)
    _model(w, "HostModel", b"\x09" * 16, agg, "host-a")
    _model(w, "DomainModel", dom12, agg)
    _model(w, "DomainParticipantModel", part, statuses[0], "router#source")
    _model(w, "DataWriterModel", writer, statuses[1], "umfvitals", "umfvitals::T")
    _model(w, "DataReaderModel", reader, statuses[2], "umfcmd", "umfcmd::T")
    _model(w, "DomainTopicModel", dom12, agg, "umfvitals", "umfvitals::T")
    _model(w, "DomainTopicModel", dom13, agg, "umfvitals", "umfvitals::T")
    return w.data()


MONITOR_SER = monitor_session()


def rti_upload(data: bytes, kind: str) -> str:
    return uploads.save(io.BytesIO(data), kind)[0]


def import_collector(tmp_path, artifact: str, upload_id: str) -> RtiCollector:
    r = RtiCollector(LIMITS)
    r.configure({"mode": "import", "artifact": artifact, "upload_id": upload_id})
    r.bind_job("job-1", tmp_path)
    return r


def test_import_admin_console_summarizes_topology(tmp_path):
    r = import_collector(tmp_path, "admin_console", rti_upload(ADMIN_XML, "rti_admin"))
    result = r.run("")   # import mode ignores the target

    assert result.summary == ("Admin Console export: 2 participants on domains 12, 13, "
                              "3 topics, 3 writers, 2 readers")
    assert result.warnings == []
    assert r.audit_fields() == {"mode": "import", "artifact": "admin_console", "upload_id": r.upload_id}

    saved = json.loads((tmp_path / "rti.json").read_text())
    assert saved["source"] == "admin_console_export" and saved["is_discovery_export"]
    assert saved["export_version"] == "6.1.2.23"
    assert saved["exported_at"] == "2026-11-24 20:17:30.406 UTC"
    assert saved["processes"] == 1 and saved["domains"] == [12, 13]
    assert saved["topic_names"] == ["umfalarm", "umfcmd", "umfvitals"]
    assert saved["types"] == ["umfalarm::T", "umfcmd::T", "umfvitals::T"]
    assert (tmp_path / "rti_admin_console.xml").read_bytes() == ADMIN_XML   # raw export kept


def test_admin_console_export_participants_and_per_domain_topics():
    found = summarize_admin_console(ADMIN_XML)
    src, view = found["participants"]
    assert src == {"domain_id": 12, "name": "router#source", "product_version": "6.1.2.31",
                   "process_id": "8", "service": "router-x", "monitoring_library": True,
                   "writers": 2, "readers": 2}
    assert view["domain_id"] == 13 and view["monitoring_library"] is False
    assert (view["writers"], view["readers"]) == (1, 0)
    # A topic is counted per domain: umfvitals is on both 12 and 13.
    assert found["topics"] == [
        {"domain_id": 12, "topic": "umfalarm", "type": "umfalarm::T", "writers": 1, "readers": 0},
        {"domain_id": 12, "topic": "umfcmd", "type": "umfcmd::T", "writers": 0, "readers": 1},
        {"domain_id": 12, "topic": "umfvitals", "type": "umfvitals::T", "writers": 1, "readers": 1},
        {"domain_id": 13, "topic": "umfvitals", "type": "umfvitals::T", "writers": 1, "readers": 0},
    ]
    assert (found["writers"], found["readers"]) == (3, 2)


def test_admin_console_export_other_xml_is_flagged(tmp_path):
    other = b"<?xml version='1.0'?><dds><qos_library name='x'/></dds>"   # e.g. a QoS file
    r = import_collector(tmp_path, "admin_console", rti_upload(other, "rti_admin"))
    result = r.run("")
    assert any("not an Admin Console discovery export" in w for w in result.warnings)
    assert any("no participants were recognized" in w for w in result.warnings)


def test_import_monitor_session_is_summarized(tmp_path):
    r = import_collector(tmp_path, "monitor", rti_upload(MONITOR_SER, "rti_monitor"))
    result = r.run("")

    assert result.summary == ("RTI Monitor session: 1 participants on domains 12, 13, "
                              "1 topics, 1 writers, 1 readers; no statistics")
    assert len(result.warnings) == 1 and "no Monitor statistics" in result.warnings[0]
    saved = json.loads((tmp_path / "rti.json").read_text())
    assert saved["source"] == "monitor_session" and saved["is_monitor_session"]
    assert saved["sha256"] == hashlib.sha256(MONITOR_SER).hexdigest()
    assert saved["monitor_version"] == "6.1.2.23" and saved["declared_entities"] == 8
    assert saved["participants"] == ["router#source"] and saved["hosts"] == ["host-a"]
    assert saved["topics"] == [
        {"domain_id": 12, "topic": "umfvitals", "type": "umfvitals::T"},
        {"domain_id": 13, "topic": "umfvitals", "type": "umfvitals::T"},
    ]
    assert saved["monitoring_status"] == {"MONITORING_SUPPORTED_NO_DATA_AVAILABLE": 3,
                                          "MONITORING_SUPPORTED_THROUGH_AGGREGATION": 5}
    assert saved["has_statistics"] is False
    assert (tmp_path / "rti_monitor.ser").read_bytes() == MONITOR_SER   # raw session kept


def test_monitor_session_with_statistics_is_flagged():
    # Any entity status other than "no data" / "through aggregation" means RTI
    # recorded statistics for it. (The bench has none yet; the name is RTI's
    # pattern for a status with data.)
    found = summarize_monitor_session(monitor_session(
        ("MONITORING_SUPPORTED_DATA_AVAILABLE",) + ("MONITORING_SUPPORTED_NO_DATA_AVAILABLE",) * 2))
    assert found["has_statistics"] is True


def test_monitor_session_count_mismatch_warns(tmp_path):
    data = bytearray(MONITOR_SER)
    i = data.index(struct.pack(">i", 8))     # the declared entity count
    data[i:i + 4] = struct.pack(">i", 9)
    r = import_collector(tmp_path, "monitor", rti_upload(bytes(data), "rti_monitor"))
    assert any("lists 9 entities but 8 were read" in w for w in r.run("").warnings)


def test_other_java_stream_is_flagged(tmp_path):
    w = W()
    w.string("just a string")
    r = import_collector(tmp_path, "monitor", rti_upload(w.data(), "rti_monitor"))
    result = r.run("")
    assert any("not an RTI Monitor saved session" in m for m in result.warnings)


def test_unreadable_monitor_session_fails_cleanly(tmp_path):
    bad = MONITOR_SER[:len(MONITOR_SER) // 2]           # truncated mid-stream
    r = import_collector(tmp_path, "monitor", rti_upload(bad, "rti_monitor"))
    with pytest.raises(PluginError, match="could not be read"):
        r.run("")
    assert (tmp_path / "rti_monitor.ser").read_bytes() == bad   # raw file still kept


def test_import_mode_works_without_the_image(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "rti_enabled", False)   # no rtitools image needed to read a file
    r = import_collector(tmp_path, "admin_console", rti_upload(ADMIN_XML, "rti_admin"))
    assert r.mode == "import"


def test_import_needs_a_known_artifact_and_a_real_upload(tmp_path):
    good = rti_upload(ADMIN_XML, "rti_admin")
    for bad in ({"mode": "import"},                                                # no artifact
                {"mode": "import", "artifact": "nope", "upload_id": good},         # unknown artifact
                {"mode": "import", "artifact": "admin_console"},                   # no upload_id
                {"mode": "import", "artifact": "admin_console", "upload_id": "../x"},     # bad id
                {"mode": "import", "artifact": "admin_console", "upload_id": "0" * 32},   # not on disk
                {"mode": "import", "artifact": "admin_console", "upload_id": good, "domain": 7}):  # stray
        with pytest.raises(PluginConfigError):
            RtiCollector(LIMITS).configure(bad)
    RtiCollector(LIMITS).configure({"mode": "import", "artifact": "admin_console", "upload_id": good})


def test_mode_must_be_run_or_import():
    with pytest.raises(PluginConfigError, match="mode must be"):
        RtiCollector(LIMITS).configure({"mode": "sideways"})


def test_run_mode_rejects_import_only_options(enabled):
    for bad in ({"artifact": "monitor"}, {"upload_id": "0" * 32}):
        with pytest.raises(PluginConfigError, match="run mode does not accept"):
            RtiCollector(LIMITS).configure(bad)


def test_admin_console_export_refuses_dtd_and_bad_xml():
    with pytest.raises(PluginError, match="DTD or entities"):
        summarize_admin_console(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>')
    # refused even past the first few KB (e.g. after a long leading comment)
    late = b"<!--" + b"x" * 8192 + b'--><!DOCTYPE x [<!ENTITY a "b">]><x/>'
    with pytest.raises(PluginError, match="DTD or entities"):
        summarize_admin_console(late)
    with pytest.raises(PluginError, match="not valid XML"):
        summarize_admin_console(b"<?xml version='1.0'?><unclosed>")


def test_admin_console_export_with_no_topology_warns(tmp_path):
    r = import_collector(tmp_path, "admin_console", rti_upload(b"<?xml version='1.0'?><empty/>", "rti_admin"))
    result = r.run("")
    assert result.summary == ("Admin Console export: 0 participants on domains none, "
                              "0 topics, 0 writers, 0 readers")
    assert any("no participants were recognized" in w for w in result.warnings)


def test_rti_uploads_enforce_magic_bytes():
    rti_upload(ADMIN_XML, "rti_admin")                        # accepted: starts with <!--
    rti_upload(b"<?xml version='1.0'?><system/>", "rti_admin")   # accepted: XML declaration
    rti_upload(MONITOR_SER, "rti_monitor")                    # accepted
    with pytest.raises(uploads.UploadError, match="not a rti_admin file"):
        uploads.save(io.BytesIO(b"not xml at all"), "rti_admin")
    with pytest.raises(uploads.UploadError, match="not a rti_monitor file"):
        uploads.save(io.BytesIO(b"<?xml version='1.0'?><dds/>"), "rti_monitor")
