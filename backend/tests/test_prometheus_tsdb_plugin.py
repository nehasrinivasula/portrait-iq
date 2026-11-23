"""
SCRUM-105: the Prometheus TSDB export collector. The cluster, the pod and the
`tar`/compress inside it are faked -- the FakeRunner's exec() writes a small
stand-in file where the real pod would write the archive onto the data volume.
No real Prometheus, node or kubectl is touched.
"""
import pytest

from app.collectors import k8s
from app.collectors.base import PluginConfigError, PluginError, ResourceLimits
from app.collectors.prometheus_tsdb import PrometheusTsdbCollector
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


def configured(tmp_path, monkeypatch, runner) -> PrometheusTsdbCollector:
    monkeypatch.setattr(k8s, "get_pod_runner", lambda: runner)
    c = PrometheusTsdbCollector(LIMITS)
    c.configure({})
    c.bind_job("job-1", tmp_path)
    return c


# --- configure gating -----------------------------------------------------

def test_off_unless_hostpath_set(monkeypatch):
    monkeypatch.setattr(settings, "prometheus_tsdb_hostpath", "")
    with pytest.raises(PluginConfigError, match="PROMETHEUS_TSDB_HOSTPATH is not set"):
        PrometheusTsdbCollector(LIMITS).configure({})


def test_takes_no_options(enabled):
    with pytest.raises(PluginConfigError, match="takes no options"):
        PrometheusTsdbCollector(LIMITS).configure({"scope": "7d"})


def test_rejects_unimplemented_scope(enabled, monkeypatch):
    monkeypatch.setattr(settings, "prometheus_tsdb_scope", "7d")
    with pytest.raises(PluginConfigError, match="only 'full' is implemented"):
        PrometheusTsdbCollector(LIMITS).configure({})


def test_rejects_bad_compressor(enabled, monkeypatch):
    monkeypatch.setattr(settings, "prometheus_tsdb_compressor", "lzma")
    with pytest.raises(PluginConfigError, match="compressor must be one of"):
        PrometheusTsdbCollector(LIMITS).configure({})


# --- a successful export --------------------------------------------------

def test_exports_and_returns_a_native_archive(tmp_path, monkeypatch, enabled):
    artifact = tmp_path / "prometheus_tsdb.tar.zst"
    runner = FakeRunner(write_to=artifact)
    c = configured(tmp_path, monkeypatch, runner)

    result = c.run("192.0.2.3")

    assert result.tool == "prometheus_tsdb"
    assert result.artifact_paths == [str(artifact)]
    assert artifact.read_bytes() == b"PROM-TSDB-ARCHIVE"
    assert "exported" in result.summary.lower()
    # The unmasked nature is surfaced as a warning (pairs with Neha/Hudson).
    assert any("unmasked" in w for w in result.warnings)


def test_pod_is_root_with_hostpath_read_and_pvc_write(tmp_path, monkeypatch, enabled):
    artifact = tmp_path / "prometheus_tsdb.tar.zst"
    runner = FakeRunner(write_to=artifact)
    configured(tmp_path, monkeypatch, runner).run("192.0.2.3")

    kw = runner.created[0]
    assert kw["run_as_user"] == 0
    vols = {v["name"]: v for v in kw["extra_volumes"]}
    assert vols["tsdb-src"]["hostPath"]["path"] == HOSTPATH
    assert vols["tsdb-out"]["persistentVolumeClaim"]["claimName"] == "portrait-iq-data"
    mounts = {m["name"]: m for m in kw["extra_mounts"]}
    assert mounts["tsdb-src"]["mountPath"] == "/src" and mounts["tsdb-src"]["readOnly"] is True
    assert mounts["tsdb-out"]["mountPath"] == "/data"


def test_export_script_tars_throttled_with_guards(tmp_path, monkeypatch, enabled):
    artifact = tmp_path / "prometheus_tsdb.tar.zst"
    runner = FakeRunner(write_to=artifact)
    configured(tmp_path, monkeypatch, runner).run("192.0.2.3")

    argv = runner.execs[0]
    assert argv[0] == "sh" and argv[1] == "-c"
    script = argv[2]
    assert "ionice -c3" in script and "nice -n 19" in script          # throttled for etcd
    assert "tar -C \"$SRC\" -cf - ." in script and "zstd -T0 -3 -q" in script
    assert "disk guard" in script and "over cap" in script            # refuses, never fills disk
    assert str(artifact) in script                                    # writes to the PVC path


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
    # The raw stderr stays out of the UI-facing reason (SCRUM-68).
    assert "did not have room" in (ei.value.safe_reason or "")
    assert "KB" not in (ei.value.safe_reason or "")
    assert not (tmp_path / "prometheus_tsdb.tar.zst").exists()


def test_over_cap_failure_is_a_safe_error(tmp_path, monkeypatch, enabled):
    runner = FakeRunner(exit_code=4, stderr="over cap")
    with pytest.raises(PluginError) as ei:
        configured(tmp_path, monkeypatch, runner).run("192.0.2.3")
    assert "larger than the configured export limit" in (ei.value.safe_reason or "")


def test_zero_exit_but_no_file_fails(tmp_path, monkeypatch, enabled):
    runner = FakeRunner(exit_code=0, write_to=None)      # exec "succeeds" but writes nothing
    with pytest.raises(PluginError) as ei:
        configured(tmp_path, monkeypatch, runner).run("192.0.2.3")
    assert "produced no file" in (ei.value.safe_reason or "")


# --- lifecycle ------------------------------------------------------------

def test_cleanup_deletes_the_pod(tmp_path, monkeypatch, enabled):
    artifact = tmp_path / "prometheus_tsdb.tar.zst"
    runner = FakeRunner(write_to=artifact)
    c = configured(tmp_path, monkeypatch, runner)
    c.run("192.0.2.3")
    pod = c._pod                      # set during run(), cleared by cleanup()
    c.cleanup()
    assert runner.deleted == [pod]
    assert c._pod is None


def test_cleanup_safe_before_run(enabled):
    PrometheusTsdbCollector(LIMITS).cleanup()      # must not raise


def test_audit_records_the_privileged_read(enabled):
    c = PrometheusTsdbCollector(LIMITS)
    c.configure({})
    assert c.audit_fields() == {"prometheus_tsdb_scope": "full",
                                "prometheus_tsdb_hostpath": HOSTPATH}
