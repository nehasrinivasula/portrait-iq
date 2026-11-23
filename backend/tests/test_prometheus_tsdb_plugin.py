"""
SCRUM-105: the Prometheus TSDB export collector. The cluster, the pod and the
`tar`/compress inside it are faked -- the FakeRunner's exec() writes a small
stand-in file where the real pod would write the archive onto the data volume.
No real Prometheus, node or kubectl is touched.
"""
import pytest

from app.collectors import k8s
from app.collectors.base import PluginConfigError, PluginError, ResourceLimits
from app.collectors.prometheus_tsdb import PrometheusTsdbCollector, _window_minutes
from app.config import settings

LIMITS = ResourceLimits(cpu_pct=50, mem_mb=1024, max_output_mb=30000)
HOSTPATH = "/opt/local-path-provisioner/pvc-abc_compute_prometheus-db-0"


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "prometheus_tsdb_hostpath", HOSTPATH)
    monkeypatch.setattr(settings, "prometheus_tsdb_scope", "full")
    monkeypatch.setattr(settings, "prometheus_tsdb_compressor", "zstd")
    monkeypatch.setattr(settings, "prometheus_tsdb_data_pvc", "portrait-iq-data")
    monkeypatch.setattr(settings, "prometheus_tsdb_data_mount", "/data")


class FakeRunner:
    """Stands in for k8s.PodRunner. On a 0-exit exec it writes `content` to
    `write_to`, imitating the pod writing the archive onto the shared PVC."""
    def __init__(self, *, exit_code=0, stderr="", write_to=None, content=b"PROM-TSDB-ARCHIVE"):
        self.exit_code, self.stderr = exit_code, stderr
        self.write_to, self.content = write_to, content
        self.created, self.execs, self.deleted = [], [], []

    def create(self, **kw):
        self.created.append(kw)

    def wait_running(self, name, timeout_s):
        return ""

    def exec(self, name, argv, timeout_s):
        self.execs.append(argv)
        if self.exit_code == 0 and self.write_to is not None:
            self.write_to.write_bytes(self.content)
        return k8s.ExecResult(self.exit_code, "", self.stderr)

    def delete(self, name):
        self.deleted.append(name)


def configured(tmp_path, monkeypatch, runner, **options) -> PrometheusTsdbCollector:
    monkeypatch.setattr(k8s, "get_pod_runner", lambda: runner)
    c = PrometheusTsdbCollector(LIMITS)
    c.configure(options)
    c.bind_job("job-1", tmp_path)
    return c


# --- configure gating -----------------------------------------------------

def test_off_unless_hostpath_set(monkeypatch):
    monkeypatch.setattr(settings, "prometheus_tsdb_hostpath", "")
    with pytest.raises(PluginConfigError, match="PROMETHEUS_TSDB_HOSTPATH is not set"):
        PrometheusTsdbCollector(LIMITS).configure({})


def test_rejects_unknown_option(enabled):
    with pytest.raises(PluginConfigError, match="unexpected options"):
        PrometheusTsdbCollector(LIMITS).configure({"window": "7d"})


def test_scope_defaults_to_the_setting(enabled):
    c = PrometheusTsdbCollector(LIMITS)
    c.configure({})
    assert c.options == {"scope": "full"}


@pytest.mark.parametrize("scope", ["full", "7d", "30d", "24h", "1h"])
def test_accepts_full_and_window_scopes(enabled, scope):
    c = PrometheusTsdbCollector(LIMITS)
    c.configure({"scope": scope})
    assert c.options["scope"] == scope


@pytest.mark.parametrize("scope", ["week", "7", "7m", "-3d", "0x", "full "])
def test_rejects_bad_scope(enabled, scope):
    with pytest.raises(PluginConfigError, match="must be 'full' or a window"):
        PrometheusTsdbCollector(LIMITS).configure({"scope": scope})


def test_window_minutes():
    assert _window_minutes("full") == 0
    assert _window_minutes("7d") == 7 * 1440
    assert _window_minutes("24h") == 24 * 60


def test_rejects_bad_compressor(enabled, monkeypatch):
    monkeypatch.setattr(settings, "prometheus_tsdb_compressor", "lzma")
    with pytest.raises(PluginConfigError, match="compressor must be one of"):
        PrometheusTsdbCollector(LIMITS).configure({})


# --- a successful export --------------------------------------------------

def test_exports_and_returns_a_native_archive(tmp_path, monkeypatch, enabled):
    artifact = tmp_path / "prometheus_tsdb.tar.zst"
    runner = FakeRunner(write_to=artifact)
    result = configured(tmp_path, monkeypatch, runner).run("192.0.2.3")

    assert result.tool == "prometheus_tsdb"
    assert result.artifact_paths == [str(artifact)]
    assert artifact.read_bytes() == b"PROM-TSDB-ARCHIVE"
    assert "exported" in result.summary.lower() and "full database" in result.summary
    assert any("unmasked" in w for w in result.warnings)


def test_window_scope_in_summary(tmp_path, monkeypatch, enabled):
    artifact = tmp_path / "prometheus_tsdb.tar.zst"
    runner = FakeRunner(write_to=artifact)
    result = configured(tmp_path, monkeypatch, runner, scope="7d").run("192.0.2.3")
    assert "last 7d" in result.summary


def test_pod_is_root_with_dac_override_hostpath_read_and_pvc_write(tmp_path, monkeypatch, enabled):
    artifact = tmp_path / "prometheus_tsdb.tar.zst"
    runner = FakeRunner(write_to=artifact)
    configured(tmp_path, monkeypatch, runner).run("192.0.2.3")

    kw = runner.created[0]
    assert kw["run_as_user"] == 0
    assert kw["add_capabilities"] == ("DAC_OVERRIDE",)    # write into the backend-owned dir
    vols = {v["name"]: v for v in kw["extra_volumes"]}
    assert vols["tsdb-src"]["hostPath"]["path"] == HOSTPATH
    assert vols["tsdb-out"]["persistentVolumeClaim"]["claimName"] == "portrait-iq-data"
    mounts = {m["name"]: m for m in kw["extra_mounts"]}
    assert mounts["tsdb-src"]["mountPath"] == "/src" and mounts["tsdb-src"]["readOnly"] is True
    assert mounts["tsdb-out"]["mountPath"] == "/data"


def test_full_export_script(tmp_path, monkeypatch, enabled):
    artifact = tmp_path / "prometheus_tsdb.tar.zst"
    runner = FakeRunner(write_to=artifact)
    configured(tmp_path, monkeypatch, runner).run("192.0.2.3")

    argv = runner.execs[0]
    assert argv[0] == "sh" and argv[1] == "-c"
    script = argv[2]
    assert "WIN=0" in script                                      # full: no window
    assert 'SEL="$DB"' in script
    assert "ionice -c3" in script and "nice -n 19" in script      # throttled for etcd
    assert "zstd -T0 -3 -q" in script and "disk guard" in script and "over cap" in script
    assert str(artifact) in script


def test_windowed_export_script_selects_recent_blocks(tmp_path, monkeypatch, enabled):
    artifact = tmp_path / "prometheus_tsdb.tar.zst"
    runner = FakeRunner(write_to=artifact)
    configured(tmp_path, monkeypatch, runner, scope="7d").run("192.0.2.3")

    script = runner.execs[0][2]
    assert "WIN=10080" in script                                  # 7 * 1440 minutes
    assert "find \"$DB\" -maxdepth 1 -type d -name '01*' -mmin -\"$WIN\"" in script
    assert 'SEL="$DB/wal $DB/chunks_head $BLOCKS"' in script


def test_gzip_changes_the_extension(tmp_path, monkeypatch, enabled):
    monkeypatch.setattr(settings, "prometheus_tsdb_compressor", "gzip")
    artifact = tmp_path / "prometheus_tsdb.tar.gz"
    runner = FakeRunner(write_to=artifact)
    result = configured(tmp_path, monkeypatch, runner).run("192.0.2.3")
    assert result.artifact_paths == [str(artifact)]
    assert "gzip -3 -c" in runner.execs[0][2]


# --- failure paths --------------------------------------------------------

def test_disk_guard_failure_is_a_safe_error(tmp_path, monkeypatch, enabled):
    runner = FakeRunner(exit_code=3, stderr="disk guard: need 14000000KB, only 500KB free")
    c = configured(tmp_path, monkeypatch, runner)
    with pytest.raises(PluginError) as ei:
        c.run("192.0.2.3")
    assert "did not have room" in (ei.value.safe_reason or "")
    assert "KB" not in (ei.value.safe_reason or "")               # raw stderr stays out of the UI
    assert not (tmp_path / "prometheus_tsdb.tar.zst").exists()


def test_over_cap_failure_is_a_safe_error(tmp_path, monkeypatch, enabled):
    runner = FakeRunner(exit_code=4, stderr="over cap")
    with pytest.raises(PluginError) as ei:
        configured(tmp_path, monkeypatch, runner).run("192.0.2.3")
    assert "larger than the configured export limit" in (ei.value.safe_reason or "")


def test_zero_exit_but_no_file_fails(tmp_path, monkeypatch, enabled):
    runner = FakeRunner(exit_code=0, write_to=None)
    with pytest.raises(PluginError) as ei:
        configured(tmp_path, monkeypatch, runner).run("192.0.2.3")
    assert "produced no file" in (ei.value.safe_reason or "")


# --- lifecycle ------------------------------------------------------------

def test_cleanup_deletes_the_pod(tmp_path, monkeypatch, enabled):
    artifact = tmp_path / "prometheus_tsdb.tar.zst"
    runner = FakeRunner(write_to=artifact)
    c = configured(tmp_path, monkeypatch, runner)
    c.run("192.0.2.3")
    pod = c._pod
    c.cleanup()
    assert runner.deleted == [pod]
    assert c._pod is None


def test_cleanup_safe_before_run(enabled):
    PrometheusTsdbCollector(LIMITS).cleanup()


def test_audit_records_scope_and_read(enabled):
    c = PrometheusTsdbCollector(LIMITS)
    c.configure({"scope": "24h"})
    assert c.audit_fields() == {"prometheus_tsdb_scope": "24h",
                                "prometheus_tsdb_hostpath": HOSTPATH}
