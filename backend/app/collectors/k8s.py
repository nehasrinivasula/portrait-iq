"""
Kubernetes access for plugins that deploy a tool pod (SCRUM-39).

Every tool pod created here:
  - lives in settings.tools_namespace (ours), never in Portrait's pm or
    GE's compute namespace;
  - carries TOOL_LABELS plus its job and tool, so cleanup() and the startup
    sweep can find it;
  - has activeDeadlineSeconds, so Kubernetes stops it even if this backend
    dies before cleanup() runs (C-5);
  - gets CPU and memory limits from the job's ResourceLimits (R-4);
  - gets no service account token: tools never call the Kubernetes API.

Plugins call get_pod_runner() in run(), never in configure(), so choosing
a tool needs no cluster. Tests replace get_pod_runner with a fake.

The backend's own service account needs, in its namespace only: pods
create/get/list/delete and pods/exec create (deploy/k8s/tool-rbac.yaml).
"""
import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kubernetes.client.exceptions import ApiException

from app.collectors.base import PluginError, ResourceLimits
from app.config import settings

log = logging.getLogger(__name__)

TOOL_LABELS = {"app.kubernetes.io/part-of": "portrait-iq", "portrait-iq/role": "tool"}
TOOL_SELECTOR = ",".join(f"{k}={v}" for k, v in TOOL_LABELS.items())
MAX_EXEC_OUTPUT = 256 * 1024    # per stream: a check's output, not a capture
API_TIMEOUT_S = 15              # one Kubernetes API call; exec streams have their own


@dataclass
class ExecResult:
    exit_code: int | None    # None: timed out, or the stream broke
    stdout: str
    stderr: str
    truncated: bool = False


def pod_name(tool: str, job_id: str | None) -> str:
    """Unique per run, and readable in `kubectl get pods`."""
    job = (job_id or "adhoc")[:8]
    return f"piq-{tool.replace('_', '-')}-{job}-{secrets.token_hex(2)}"


def pod_resources(limits: ResourceLimits) -> dict:
    """cpu_pct is a percentage of one CPU core (50 -> 500m). Requests are
    tiny so a tool still schedules on a busy node; limits are the ceiling."""
    return {
        "requests": {"cpu": "10m", "memory": "32Mi"},
        "limits": {"cpu": f"{max(limits.cpu_pct, 1) * 10}m", "memory": f"{limits.mem_mb}Mi"},
    }


def _api_message(e: ApiException) -> str:
    """The short reason Kubernetes gives, e.g. 'pods is forbidden: ...'."""
    try:
        return json.loads(e.body or "{}").get("message") or e.reason or str(e.status)
    except (ValueError, AttributeError):
        return e.reason or str(e.status)


def _load_core_api():
    from kubernetes import client, config

    try:
        config.load_incluster_config()
    except config.ConfigException:
        try:
            config.load_kube_config(config_file=settings.kubeconfig_path)
        except Exception as e:  # noqa: BLE001  missing or unreadable kubeconfig
            raise PluginError(
                "No Kubernetes access: run Portrait IQ as a pod in the cluster, or set "
                f"KUBECONFIG_PATH to a kubeconfig for it ({type(e).__name__})."
            ) from None
    return client.CoreV1Api()


class PodRunner:
    def __init__(self, namespace: str, core_api=None, sleep: Callable[[float], None] = time.sleep):
        self.namespace = namespace
        self._api = core_api
        self._sleep = sleep

    @property
    def api(self):
        if self._api is None:
            self._api = _load_core_api()
        return self._api

    # -- lifecycle ---------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        tool: str,
        job_id: str | None,
        image: str,
        command: list[str],
        deadline_s: int,
        limits: ResourceLimits,
        host_network: bool = False,
        add_capabilities: tuple[str, ...] = (),
        tmp_size_mb: int = 64,
    ) -> None:
        """Start a pod that runs command (usually a long sleep) so the plugin
        can exec its checks in it. Only /tmp is writable."""
        labels = {
            **TOOL_LABELS,
            "portrait-iq/tool": tool.replace("_", "-"),
            "portrait-iq/job": job_id or "adhoc",
        }
        body = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": name, "labels": labels},
            "spec": {
                "restartPolicy": "Never",
                "activeDeadlineSeconds": deadline_s,
                "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "terminationGracePeriodSeconds": 1,
                "hostNetwork": host_network,
                # With host networking, still resolve cluster service names.
                "dnsPolicy": "ClusterFirstWithHostNet" if host_network else "ClusterFirst",
                "containers": [{
                    "name": "tool",
                    "image": image,
                    "imagePullPolicy": settings.tool_image_pull_policy,
                    "command": command,
                    "env": [{"name": "HOME", "value": "/tmp"}],
                    "resources": pod_resources(limits),
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"], "add": list(add_capabilities)},
                    },
                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                }],
                "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": f"{tmp_size_mb}Mi"}}],
            },
        }
        try:
            self.api.create_namespaced_pod(self.namespace, body, _request_timeout=API_TIMEOUT_S)
        except ApiException as e:
            raise PluginError(f"could not create the {tool} pod: {_api_message(e)}") from None

    def wait_running(self, name: str, timeout_s: int) -> None:
        """Wait for the pod to run, or fail with why it is still pending
        (e.g. 'Unschedulable: ... Too many pods' or 'ErrImageNeverPull')."""
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                pod = self.api.read_namespaced_pod(name, self.namespace, _request_timeout=API_TIMEOUT_S)
            except ApiException as e:
                raise PluginError(f"could not read pod {name}: {_api_message(e)}") from None
            phase = pod.status.phase
            if phase == "Running":
                return
            if phase in ("Succeeded", "Failed"):
                raise PluginError(f"pod {name} stopped before the tool ran ({phase})")
            if time.monotonic() >= deadline:
                why = _pending_reason(pod) or phase or "unknown"
                raise PluginError(f"pod {name} did not start within {timeout_s}s: {why}")
            self._sleep(2)

    def delete(self, name: str) -> None:
        """Delete the pod now. Already gone is fine."""
        try:
            self.api.delete_namespaced_pod(
                name, self.namespace, grace_period_seconds=0, _request_timeout=API_TIMEOUT_S
            )
        except ApiException as e:
            if e.status == 404:
                return
            raise PluginError(f"could not delete pod {name}: {_api_message(e)}") from None

    def sweep(self) -> list[str]:
        """Delete every tool pod in our namespace. For startup only, when no
        job of this backend can be running: these are leftovers of a crash."""
        pods = self.api.list_namespaced_pod(
            self.namespace, label_selector=TOOL_SELECTOR, _request_timeout=API_TIMEOUT_S
        )
        names = [p.metadata.name for p in pods.items]
        for name in names:
            self.delete(name)
        return names

    # -- exec --------------------------------------------------------------

    def exec(self, name: str, argv: list[str], timeout_s: int) -> ExecResult:
        """Run argv in the pod (no shell) and collect its output, at most
        MAX_EXEC_OUTPUT per stream."""
        out, err = _Capped(), _Capped()
        code = self._stream(name, argv, timeout_s, out.add, err.add)
        return ExecResult(code, out.text(), err.text(), out.truncated or err.truncated)

    def read_file(self, name: str, path: str, dest: Path, max_bytes: int) -> int:
        """Copy a file out of the pod to dest; returns its size. Sent as
        base64, since exec streams are text."""
        size_res = self.exec(name, ["stat", "-c", "%s", path], 30)
        if size_res.exit_code != 0:
            raise PluginError(f"{path} was not produced in pod {name}")
        size = int(size_res.stdout.strip())
        if size > max_bytes:
            raise PluginError(f"{path} is {size} bytes, over the {max_bytes}-byte limit")

        err = _Capped()
        decoder = _Base64Writer(dest)
        try:
            code = self._stream(name, ["base64", path], 60 + size // 500_000, decoder.add, err.add)
            decoder.finish()
        except BaseException:
            decoder.abort()
            raise
        if code != 0 or decoder.written != size:
            dest.unlink(missing_ok=True)
            raise PluginError(f"copying {path} out of pod {name} failed: {err.text()[:200]}")
        return size

    def _stream(self, name, argv, timeout_s, on_stdout, on_stderr) -> int | None:
        from kubernetes.stream import stream

        try:
            resp = stream(
                self.api.connect_get_namespaced_pod_exec, name, self.namespace,
                command=argv, stdout=True, stderr=True, stdin=False, tty=False,
                _preload_content=False,
            )
        except ApiException as e:
            raise PluginError(f"could not run {argv[0]} in pod {name}: {_api_message(e)}") from None
        deadline = time.monotonic() + timeout_s
        try:
            while resp.is_open():
                if time.monotonic() >= deadline:
                    return None
                resp.update(timeout=1)
                if resp.peek_stdout():
                    on_stdout(resp.read_stdout())
                if resp.peek_stderr():
                    on_stderr(resp.read_stderr())
            # Whatever arrived together with the close.
            on_stdout(resp.read_stdout(timeout=0) or "")
            on_stderr(resp.read_stderr(timeout=0) or "")
            try:
                return resp.returncode
            except (TypeError, KeyError, IndexError, ValueError):
                return None   # closed without a status: the stream broke
        finally:
            resp.close()


def _pending_reason(pod) -> str:
    for cs in (pod.status.container_statuses or []):
        waiting = cs.state and cs.state.waiting
        if waiting and waiting.reason:
            return f"{waiting.reason}: {waiting.message or ''}".strip(": ")
    for cond in (pod.status.conditions or []):
        if cond.type == "PodScheduled" and cond.status == "False":
            return f"{cond.reason}: {cond.message or ''}".strip(": ")
    return ""


class _Capped:
    def __init__(self, limit: int = MAX_EXEC_OUTPUT):
        self.parts: list[str] = []
        self.size = 0
        self.limit = limit
        self.truncated = False

    def add(self, chunk: str) -> None:
        room = self.limit - self.size
        if len(chunk) > room:
            chunk, self.truncated = chunk[:room], True
        self.parts.append(chunk)
        self.size += len(chunk)

    def text(self) -> str:
        return "".join(self.parts)


class _Base64Writer:
    """Decodes base64 text as it streams in and writes the bytes to dest."""

    def __init__(self, dest: Path):
        self.dest = dest
        self.part = dest.with_name(dest.name + ".part")
        self.f = open(self.part, "wb")
        self.pending = ""
        self.written = 0

    def add(self, chunk: str) -> None:
        data = self.pending + "".join(chunk.split())
        usable = len(data) - len(data) % 4
        if usable:
            raw = base64.b64decode(data[:usable], validate=True)
            self.f.write(raw)
            self.written += len(raw)
        self.pending = data[usable:]

    def finish(self) -> None:
        self.f.close()
        if self.pending:
            self.abort()
            raise PluginError("file copy ended mid-way")
        self.part.replace(self.dest)

    def abort(self) -> None:
        self.f.close()
        self.part.unlink(missing_ok=True)


_runner: PodRunner | None = None


def get_pod_runner() -> PodRunner:
    """The shared runner for our namespace. Connects on first use."""
    global _runner
    if _runner is None:
        _runner = PodRunner(settings.tools_namespace)
    return _runner
