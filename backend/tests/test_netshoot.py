"""
SCRUM-39: the Netshoot plugin, against a fake pod runner. Nothing here
reaches a cluster.
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  (registers tables on Base)
from app.collectors import k8s
from app.collectors.base import PluginConfigError, PluginError, ResourceLimits
from app.collectors.netshoot import NetshootCollector, is_host
from app.collectors.registry import PluginRegistry
from app.db import Base
from app.orchestrator.jobs import create_job, read_results, run_job

LIMITS = ResourceLimits(cpu_pct=50, mem_mb=512, max_output_mb=100)


class FakeRunner:
    def __init__(self, results=None, start_error=None):
        self.results = results or {}        # check argv[0] -> ExecResult
        self.start_error = start_error
        self.created, self.execs, self.deleted = [], [], []

    def create(self, **kw):
        self.created.append(kw)

    def wait_running(self, name, timeout_s):
        if self.start_error:
            raise PluginError(self.start_error)

    def exec(self, name, argv, timeout_s):
        self.execs.append(argv)
        return self.results.get(argv[0], k8s.ExecResult(0, f"{argv[0]} ok\n", ""))

    def delete(self, name):
        self.deleted.append(name)


@pytest.fixture
def runner(monkeypatch):
    r = FakeRunner()
    monkeypatch.setattr(k8s, "get_pod_runner", lambda: r)
    return r


@pytest.fixture
def plugin(tmp_path):
    p = NetshootCollector(LIMITS)
    p.configure({})
    p.bind_job("job-1", tmp_path)
    return p


def test_configure_validates_every_value():
    p = NetshootCollector(LIMITS)
    p.configure({"ports": [80], "dns_names": ["example.internal"], "extra_targets": ["192.168.0.5"]})
    assert p.options == {"ports": [80], "dns_names": ["example.internal"], "extra_targets": ["192.168.0.5"]}
    for bad in (
        {"ports": [0]}, {"ports": [70000]}, {"ports": ["22"]}, {"ports": [True]}, {"ports": 22},
        {"dns_names": ["-oProxyCommand=x"]}, {"extra_targets": ["a b"]}, {"extra_targets": ["x;rm"]},
        {"ports": list(range(1, 30))}, {"verbose": True},
    ):
        with pytest.raises(PluginConfigError):
            p.configure(bad)


@pytest.mark.parametrize("value,ok", [
    ("192.168.0.3", True), ("fe80::1", True), ("axone-log-export-tcp.pm.svc.cluster.local", True),
    ("-x", False), ("a..b", False), ("", False), (None, False), ("host name", False),
])
def test_is_host(value, ok):
    assert is_host(value) is ok


def test_run_deploys_checks_and_reports(plugin, runner, tmp_path):
    runner.results["nc"] = k8s.ExecResult(1, "", "nc: connect to 192.0.2.10 port 22 (tcp) failed: Connection refused\n")
    runner.results["dig"] = k8s.ExecResult(0, "", "")   # resolves to nothing
    result = plugin.run("192.0.2.10")

    pod = runner.created[0]
    assert pod["image"].startswith("nicolaka/netshoot:")
    assert pod["add_capabilities"] == ("NET_RAW",) and pod["job_id"] == "job-1"
    assert pod["command"][0] == "sleep" and int(pod["command"][1]) == pod["deadline_s"]
    assert ["ping", "-c", "3", "-W", "2", "192.0.2.10"] in runner.execs
    assert ["nc", "-z", "-v", "-w", "3", "192.0.2.10", "6443"] in runner.execs

    total = 3 + 2 + 2 + 3        # pod info, 2 DNS names, ping + traceroute, 3 ports
    assert result.summary == f"{total - 5} of {total} network checks passed against 192.0.2.10"
    assert "TCP 192.0.2.10:22 failed: nc: connect to 192.0.2.10 port 22 (tcp) failed: Connection refused" in result.warnings
    assert "resolve kubernetes.default.svc.cluster.local failed: no answer" in result.warnings
    records = json.loads((tmp_path / "netshoot.json").read_text())
    assert len(records) == total
    assert "== FAIL  TCP 192.0.2.10:22" in (tmp_path / "netshoot.txt").read_text()

    plugin.cleanup()
    plugin.cleanup()   # twice is fine
    assert runner.deleted == [pod["name"]]


def test_check_output_is_redacted(plugin, runner, monkeypatch):
    from app.collectors import netshoot
    monkeypatch.setattr(netshoot, "redact", lambda text: text.replace("hunter2", "[REDACTED]"))
    runner.results["cat"] = k8s.ExecResult(0, "search pm.svc hunter2\n", "")
    plugin.run("192.0.2.10")
    assert "hunter2" not in (plugin.output_dir / "netshoot.txt").read_text()


def test_bad_target_fails_before_anything_is_deployed(plugin, runner):
    with pytest.raises(PluginError, match="not an IP address"):
        plugin.run("--help")
    assert runner.created == []


def test_cleanup_before_run_is_a_no_op(runner):
    NetshootCollector(LIMITS).cleanup()
    assert runner.deleted == []


def test_a_pod_that_never_starts_is_still_deleted(tmp_path, monkeypatch):
    runner = FakeRunner(start_error="pod did not start within 120s: Unschedulable: Too many pods")
    monkeypatch.setattr(k8s, "get_pod_runner", lambda: runner)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    reg = PluginRegistry()
    reg.register(NetshootCollector)

    job = run_job(db, create_job(db, "live", ["netshoot"], LIMITS), "192.0.2.10", registry=reg)
    assert job.status == "failed"
    assert runner.deleted == [runner.created[0]["name"]]
    assert read_results(job.id) == [{
        "tool": "netshoot", "error": "pod did not start within 120s: Unschedulable: Too many pods",
    }]
