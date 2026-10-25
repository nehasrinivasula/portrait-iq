"""
SCRUM-39: the Kubernetes helper tool plugins deploy through. A fake
CoreV1Api and exec stream stand in for the cluster.
"""
import base64
from types import SimpleNamespace

import pytest
from kubernetes import stream as k8s_stream
from kubernetes.client.exceptions import ApiException

from app.collectors import k8s
from app.collectors.base import PluginError, ResourceLimits

LIMITS = ResourceLimits(cpu_pct=50, mem_mb=512, max_output_mb=100)


class FakeApi:
    def __init__(self):
        self.created, self.deleted = [], []
        self.pods = {}            # name -> list of pod objects returned by successive reads
        self.delete_error = None

    def create_namespaced_pod(self, namespace, body, **_):
        self.created.append((namespace, body))

    def read_namespaced_pod(self, name, namespace, **_):
        states = self.pods[name]
        return states.pop(0) if len(states) > 1 else states[0]

    def delete_namespaced_pod(self, name, namespace, **kw):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append((namespace, name, kw.get("grace_period_seconds")))

    def list_namespaced_pod(self, namespace, label_selector, **_):
        self.listed = (namespace, label_selector)
        return SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(name="piq-netshoot-old"))])

    def connect_get_namespaced_pod_exec(self, *a, **kw):
        raise AssertionError("only called through the fake stream")


def pod(phase, waiting=None, scheduled=None):
    statuses = None
    if waiting:
        statuses = [SimpleNamespace(state=SimpleNamespace(
            waiting=SimpleNamespace(reason=waiting[0], message=waiting[1])))]
    conditions = None
    if scheduled:
        conditions = [SimpleNamespace(type="PodScheduled", status="False",
                                      reason=scheduled[0], message=scheduled[1])]
    return SimpleNamespace(status=SimpleNamespace(phase=phase, container_statuses=statuses,
                                                  conditions=conditions))


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def runner(api):
    return k8s.PodRunner("portrait-iq", core_api=api, sleep=lambda s: None)


def test_tool_pod_is_locked_down(runner, api):
    runner.create(name="piq-netshoot-abc", tool="netshoot", job_id="job-1", image="img:1",
                  command=["sleep", "300"], deadline_s=300, limits=LIMITS, add_capabilities=("NET_RAW",))
    namespace, body = api.created[0]
    assert namespace == "portrait-iq"
    spec = body["spec"]
    assert spec["activeDeadlineSeconds"] == 300
    assert spec["automountServiceAccountToken"] is False
    assert spec["restartPolicy"] == "Never" and spec["hostNetwork"] is False
    labels = body["metadata"]["labels"]
    assert labels["portrait-iq/role"] == "tool" and labels["portrait-iq/job"] == "job-1"
    c = spec["containers"][0]
    assert c["resources"]["limits"] == {"cpu": "500m", "memory": "512Mi"}
    assert c["securityContext"]["capabilities"] == {"drop": ["ALL"], "add": ["NET_RAW"]}
    assert c["securityContext"]["allowPrivilegeEscalation"] is False
    assert c["securityContext"]["readOnlyRootFilesystem"] is True


def test_host_network_pod_still_resolves_cluster_names(runner, api):
    runner.create(name="p", tool="tshark", job_id=None, image="img", command=["sleep", "1"],
                  deadline_s=10, limits=LIMITS, host_network=True)
    spec = api.created[0][1]["spec"]
    assert spec["hostNetwork"] is True and spec["dnsPolicy"] == "ClusterFirstWithHostNet"
    assert api.created[0][1]["metadata"]["labels"]["portrait-iq/job"] == "adhoc"


def test_create_failure_says_why(runner, api):
    def forbidden(*a, **kw):
        raise ApiException(status=403, reason="Forbidden")
    api.create_namespaced_pod = forbidden
    with pytest.raises(PluginError, match="could not create the netshoot pod: Forbidden"):
        runner.create(name="p", tool="netshoot", job_id="j", image="i", command=["x"],
                      deadline_s=10, limits=LIMITS)


def test_wait_running(runner, api):
    api.pods["p"] = [pod("Pending"), pod("Pending"), pod("Running")]
    runner.wait_running("p", 60)


def test_wait_running_reports_why_a_pod_is_stuck(runner, api):
    api.pods["p"] = [pod("Pending", scheduled=("Unschedulable", "0/1 nodes are available: 1 Too many pods."))]
    with pytest.raises(PluginError, match="did not start within 0s: Unschedulable: 0/1 nodes .*Too many pods"):
        runner.wait_running("p", 0)
    api.pods["q"] = [pod("Pending", waiting=("ErrImageNeverPull", "image not present"))]
    with pytest.raises(PluginError, match="ErrImageNeverPull: image not present"):
        runner.wait_running("q", 0)
    api.pods["r"] = [pod("Failed")]
    with pytest.raises(PluginError, match="stopped before the tool ran"):
        runner.wait_running("r", 60)


def test_delete_ignores_a_pod_that_is_already_gone(runner, api):
    api.delete_error = ApiException(status=404, reason="Not Found")
    runner.delete("p")
    api.delete_error = ApiException(status=500, reason="Internal Server Error")
    with pytest.raises(PluginError, match="could not delete pod p"):
        runner.delete("p")


def test_sweep_deletes_only_tool_pods(runner, api):
    assert runner.sweep() == ["piq-netshoot-old"]
    assert api.listed == ("portrait-iq", "app.kubernetes.io/part-of=portrait-iq,portrait-iq/role=tool")
    assert api.deleted == [("portrait-iq", "piq-netshoot-old", 0)]


def test_pod_names_are_valid_and_unique():
    a, b = k8s.pod_name("hub_import", "0123456789abcdef"), k8s.pod_name("hub_import", "0123456789abcdef")
    assert a.startswith("piq-hub-import-01234567-") and a != b


# exec ---------------------------------------------------------------------

class FakeStream:
    """Mimics kubernetes.stream.ws_client.WSClient: chunks arrive on update()."""

    def __init__(self, stdout_chunks, stderr="", returncode=0, hang=False):
        self.chunks = list(stdout_chunks)
        self.stderr = stderr
        self._returncode = returncode
        self.hang = hang
        self.closed = False
        self.buf_out = self.buf_err = ""

    def is_open(self):
        return self.hang or bool(self.chunks)

    def update(self, timeout=0):
        if self.chunks:
            self.buf_out += self.chunks.pop(0)
            if not self.chunks:
                self.buf_err += self.stderr

    def peek_stdout(self):
        return self.buf_out

    def peek_stderr(self):
        return self.buf_err

    def read_stdout(self, timeout=None):
        out, self.buf_out = self.buf_out, ""
        return out

    def read_stderr(self, timeout=None):
        err, self.buf_err = self.buf_err, ""
        return err

    @property
    def returncode(self):
        return self._returncode

    def close(self):
        self.closed = True


def fake_stream(monkeypatch, *streams):
    queue = list(streams)
    calls = []

    def stream(fn, name, namespace, command, **kw):
        calls.append(command)
        return queue.pop(0)
    monkeypatch.setattr(k8s_stream, "stream", stream)
    return calls


def test_exec_collects_output_and_exit_code(runner, monkeypatch):
    s = FakeStream(["hello ", "world\n"], stderr="warn\n", returncode=3)
    calls = fake_stream(monkeypatch, s)
    res = runner.exec("p", ["echo", "hi"], 10)
    assert (res.exit_code, res.stdout, res.stderr, res.truncated) == (3, "hello world\n", "warn\n", False)
    assert calls == [["echo", "hi"]] and s.closed


def test_exec_caps_output(runner, monkeypatch):
    fake_stream(monkeypatch, FakeStream(["x" * (k8s.MAX_EXEC_OUTPUT + 10)]))
    res = runner.exec("p", ["yes"], 10)
    assert len(res.stdout) == k8s.MAX_EXEC_OUTPUT and res.truncated


def test_exec_times_out(runner, monkeypatch):
    s = FakeStream([], hang=True)
    fake_stream(monkeypatch, s)
    assert runner.exec("p", ["sleep", "999"], 0).exit_code is None
    assert s.closed


def test_read_file_round_trips_binary(runner, monkeypatch, tmp_path):
    data = bytes(range(256)) * 50
    encoded = base64.encodebytes(data).decode()      # wrapped at 76 characters, like base64(1)
    chunks = [encoded[i:i + 333] for i in range(0, len(encoded), 333)]  # split mid-line and mid-quad
    calls = fake_stream(monkeypatch, FakeStream([f"{len(data)}\n"]), FakeStream(chunks))
    dest = tmp_path / "capture.pcapng"
    assert runner.read_file("p", "/tmp/capture.pcapng", dest, max_bytes=1 << 20) == len(data)
    assert dest.read_bytes() == data
    assert calls == [["stat", "-c", "%s", "/tmp/capture.pcapng"], ["base64", "/tmp/capture.pcapng"]]
    assert not (tmp_path / "capture.pcapng.part").exists()


def test_read_file_refuses_files_over_the_limit(runner, monkeypatch, tmp_path):
    fake_stream(monkeypatch, FakeStream(["5000\n"]))
    with pytest.raises(PluginError, match="over the 100-byte limit"):
        runner.read_file("p", "/tmp/c", tmp_path / "c", max_bytes=100)


def test_read_file_removes_a_partial_copy(runner, monkeypatch, tmp_path):
    fake_stream(monkeypatch, FakeStream(["300\n"]), FakeStream(["AAAA"], returncode=1))
    with pytest.raises(PluginError, match="copying /tmp/c out of pod p failed"):
        runner.read_file("p", "/tmp/c", tmp_path / "c", max_bytes=1000)
    assert list(tmp_path.iterdir()) == []
