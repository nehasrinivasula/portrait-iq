"""
SCRUM-39: the UMF RTI plugin. The cluster and rtiddsspy are faked; the spy
output below is synthetic (documentation addresses, made-up topics).
"""
import json

import pytest

from app.collectors import k8s
from app.collectors.base import PluginConfigError, PluginError, ResourceLimits
from app.collectors.rti import RtiCollector, summarize
from app.config import settings

LIMITS = ResourceLimits(cpu_pct=50, mem_mb=512, max_output_mb=100)

SPY_OUTPUT = """\
RTI Connext DDS Spy built with DDS version: 7.3.0
source_timestamp  Info          Src HostId      topic                 type
----------------- ------------- --------------- --------------------- ----------------
1700000000.000001 New writer    from 192.0.2.21 "SyntheticVitals"     "SyntheticVitalsType"
1700000000.000002 New reader    from 192.0.2.30 "SyntheticVitals"     "SyntheticVitalsType"
1700000000.000003 New data      from 192.0.2.21 "SyntheticVitals"     "SyntheticVitalsType"
1700000000.000004 New data      from 192.0.2.21 "SyntheticVitals"     "SyntheticVitalsType"
1700000000.000005 New writer    from 192.0.2.30 "SyntheticAlarm"      "SyntheticAlarmType"
some line rtiddsspy prints that the parser does not know
"""


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "rti_enabled", True)


class FakeRunner:
    def __init__(self, exit_code=124, stdout=SPY_OUTPUT, stderr=""):
        self.exit_code, self.stdout, self.stderr = exit_code, stdout, stderr
        self.created, self.execs, self.deleted = [], [], []

    def create(self, **kw):
        self.created.append(kw)

    def wait_running(self, name, timeout_s):
        pass

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


def test_listens_without_printing_samples(tmp_path, monkeypatch, enabled):
    monkeypatch.setattr(settings, "rti_qos_file", "/opt/axone-tools/qos/example.xml")
    runner = FakeRunner()
    result = configured(tmp_path, monkeypatch, runner, duration_s=20).run("192.0.2.10")

    pod = runner.created[0]
    assert pod["host_network"] is True and pod["image"] == settings.rti_image
    assert "add_capabilities" not in pod
    argv = runner.execs[0]
    assert argv[:3] == ["timeout", "20", "rtiddsspy"]
    assert argv[argv.index("-domainId") + 1] == "12"
    assert argv[argv.index("-peer") + 1] == "192.0.2.10"
    assert argv[argv.index("-qosProfile") + 1] == "AxoneNetQoSLibrary::umfgeneral_qos"
    assert "-printSample" not in argv

    assert result.summary == "2 hosts, 2 topics on domain 12 over 20s"
    assert result.warnings == []
    saved = json.loads((tmp_path / "rti.json").read_text())
    assert saved["topics"]["SyntheticVitals"] == {
        "type": "SyntheticVitalsType", "writers": ["192.0.2.21"], "readers": ["192.0.2.30"], "samples": 2,
    }


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
    assert summarize(SPY_OUTPUT)["hosts"] == ["192.0.2.21", "192.0.2.30"]
