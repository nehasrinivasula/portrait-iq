"""
SCRUM-39: the UMF RTI plugin. The cluster and rtiddsspy are faked; the spy
output below is synthetic (documentation addresses, made-up topics).
"""
import json

import pytest

from app.collectors import k8s, rti
from app.collectors.base import PluginConfigError, PluginError, ResourceLimits
from app.collectors.rti import RtiCollector, summarize
from app.config import settings

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
