"""
The Kubernetes diagnostics plugin against a fake Kubernetes API. Nothing here
reaches a cluster. Names, images and messages below are made up.
"""
import json
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from kubernetes.client.exceptions import ApiException
from sqlalchemy.orm import sessionmaker

from app.collectors import k8s_diagnostics as kd
from app.collectors.base import PluginError, ResourceLimits
from app.collectors.k8s_diagnostics import KubernetesCollector
from app.config import settings
from app.models import Job
from app.orchestrator.jobs import create_job, read_results, run_job

LIMITS = ResourceLimits(cpu_pct=50, mem_mb=512, max_output_mb=100)
T0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
SECRET_ENV = "DB_PASSWORD=hunter2-secret"


def node(name="edge-1", ready=True, pressure=()):
    conds = [NS(type="Ready", status="True" if ready else "False", reason="KubeletReady" if ready else "KubeletNotReady",
                message="kubelet is posting ready status")]
    conds += [NS(type=p, status="True", reason=p, message=None) for p in pressure]
    return NS(metadata=NS(name=name, labels={"node-role.kubernetes.io/control-plane": "true"}, creation_timestamp=T0),
              status=NS(conditions=conds, capacity={"cpu": "16", "memory": "64Gi"},
                        allocatable={"cpu": "15", "memory": "62Gi"},
                        node_info=NS(kubelet_version="v1.34.2+k3s1", os_image="SUSE Micro",
                                     container_runtime_version="containerd://2.1.5")))


def pod(ns, name, restarts=0, ready=True, last_reason=None, phase="Running"):
    container = NS(name="main", image="example/app:1.0", env=[NS(name="DB_PASSWORD", value="hunter2-secret")],
                   command=["/bin/app", "--token=abc123secret"], args=["--verbose"],
                   resources=NS(requests={"cpu": "100m"}, limits={"memory": "256Mi"}))
    last = NS(terminated=NS(reason=last_reason, exit_code=137, finished_at=T0)) if last_reason else NS(terminated=None)
    status = NS(name="main", image="example/app:1.0", ready=ready, restart_count=restarts, last_state=last,
                state=NS(running=NS(started_at=T0), waiting=None, terminated=None))
    return NS(metadata=NS(name=name, namespace=ns, labels={"app": name}, creation_timestamp=T0,
                          annotations={"secret-note": "annotation-should-not-appear"}),
              spec=NS(node_name="edge-1", containers=[container]),
              status=NS(phase=phase, reason=None, message=None, start_time=T0,
                        conditions=[NS(type="Ready", status="True" if ready else "False")],
                        container_statuses=[status]))


def event(ns, obj, reason, minutes, type_="Warning", message="Back-off restarting failed container", count=1):
    return NS(metadata=NS(namespace=ns, creation_timestamp=T0), type=type_, reason=reason, count=count,
              involved_object=NS(kind="Pod", name=obj), message=message,
              last_timestamp=T0 + timedelta(minutes=minutes), event_time=None, first_timestamp=None)


def listing(items, more=False):
    return NS(items=items, metadata=NS(_continue="next" if more else None))


class FakeApi:
    """Records every call; only list_* methods exist, so a write would fail loudly."""

    def __init__(self, nodes=None, pods=None, events=None, errors=None):
        self.nodes, self.pods, self.events = nodes or [], pods or {}, events or {}
        self.errors = errors or {}          # ("pods", "pm") -> ApiException
        self.calls = []

    def _maybe_fail(self, key):
        if key in self.errors:
            raise self.errors[key]

    def list_node(self, **kw):
        self.calls.append(("list_node", None, kw))
        self._maybe_fail(("nodes", None))
        return listing(self.nodes)

    def list_namespaced_pod(self, ns, **kw):
        self.calls.append(("list_namespaced_pod", ns, kw))
        self._maybe_fail(("pods", ns))
        return listing(self.pods.get(ns, [])[:kw.get("limit", 10**9)], more=len(self.pods.get(ns, [])) > kw.get("limit", 10**9))

    def list_namespaced_event(self, ns, **kw):
        self.calls.append(("list_namespaced_event", ns, kw))
        self._maybe_fail(("events", ns))
        return listing(self.events.get(ns, []))


def cluster():
    return FakeApi(
        nodes=[node()],
        pods={"portrait-iq": [pod("portrait-iq", "portrait-iq-backend")],
              "pm": [pod("pm", "axonerouter-1", restarts=3, last_reason="OOMKilled"),
                     pod("pm", "configservice-0", ready=False),
                     pod("pm", "hl7-outbound-0")]},
        events={"pm": [event("pm", "axonerouter-1", "BackOff", 5, count=12),
                       event("pm", "hl7-outbound-0", "Pulled", 1, type_="Normal", message="Container image pulled"),
                       event("pm", "configservice-0", "Unhealthy", 9,
                             message="Readiness probe failed for patientName=John Smith")]},
    )


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "k8s_diagnostics_namespaces", "")

    def go(api, limits=LIMITS):
        monkeypatch.setattr(kd, "get_core_api", lambda: api)
        plugin = KubernetesCollector(limits)
        plugin.bind_job("job-1", tmp_path / "kubernetes")
        plugin.output_dir.mkdir(parents=True, exist_ok=True)
        return plugin.run("192.0.2.10"), plugin.output_dir
    return go


def read_all(out_dir):
    return {p.name: p.read_text() for p in out_dir.iterdir()}


def test_collects_nodes_pods_restarts_events_and_describe(run):
    result, out = run(cluster())
    assert result.summary == "1 of 1 node Ready; 4 pods in 2 namespaces, 1 restarted; 2 warning events"
    files = read_all(out)
    assert set(files) == {"kubernetes.json", "describe.txt", "events.txt"}
    snap = json.loads(files["kubernetes.json"])
    router = next(p for p in snap["pods"]["pm"] if p["name"] == "axonerouter-1")
    assert router["restarts"] == 3 and router["containers"][0]["last_reason"] == "OOMKilled"
    assert snap["nodes"][0]["kubelet_version"] == "v1.34.2+k3s1" and snap["nodes"][0]["roles"] == ["control-plane"]
    assert "Restart count:  3" in files["describe.txt"] and "terminated (OOMKilled, exit code 137" in files["describe.txt"]
    assert "===== Node edge-1" in files["describe.txt"]
    assert files["events.txt"].index("Unhealthy") < files["events.txt"].index("BackOff")   # newest first
    assert any("axonerouter-1 restarted 3 times, last: OOMKilled" in w for w in result.warnings)
    assert any("1 pod is not Ready" in w for w in result.warnings)


def test_details_feed_the_results_page(run):
    result, _ = run(cluster())
    d = result.details
    assert [n["name"] for n in d["nodes"]] == ["edge-1"] and d["nodes"][0]["ready"] is True
    assert [(p["name"], p["restarts"], p["last_reason"]) for p in d["problem_pods"]] == [
        ("axonerouter-1", 3, "OOMKilled"), ("configservice-0", 0, None)]
    assert [e["reason"] for e in d["warning_events"]] == ["Unhealthy", "BackOff"]
    assert {ns["name"]: ns["pods"] for ns in d["namespaces"]} == {"portrait-iq": 1, "pm": 3}
    json.dumps(d)                                                    # stored as JSON in results.json


def test_only_reads_and_only_the_configured_namespaces(run):
    api = cluster()
    run(api)
    assert {name for name, _, _ in api.calls} <= {"list_node", "list_namespaced_pod", "list_namespaced_event"}
    assert {ns for _, ns, _ in api.calls if ns} == {"portrait-iq", "pm"}
    assert all(kw.get("_request_timeout") for _, _, kw in api.calls)


def test_secrets_env_args_and_annotations_are_never_collected(run):
    result, out = run(cluster())
    everything = json.dumps(result.details) + "".join(read_all(out).values()) + json.dumps(result.warnings)
    for leaked in ("hunter2-secret", "DB_PASSWORD", "abc123secret", "--verbose", "annotation-should-not-appear"):
        assert leaked not in everything


def test_event_messages_are_masked_for_personal_data(run):
    result, out = run(cluster())
    everything = json.dumps(result.details) + "".join(read_all(out).values())
    assert "John Smith" not in everything and "[REDACTED:person_name]" in everything


def test_size_caps_keep_the_newest_events_and_say_so(run, monkeypatch):
    monkeypatch.setattr(kd, "MAX_EVENTS", 2)
    monkeypatch.setattr(kd, "MAX_PODS", 2)
    result, out = run(cluster())
    assert any("Only the newest 2 events in pm were kept." == w for w in result.warnings)
    assert any("Only the first 2 pods in pm were read." == w for w in result.warnings)
    snap = json.loads((out / "kubernetes.json").read_text())
    assert [e["reason"] for e in snap["events"]["pm"]] == ["Unhealthy", "BackOff"]


def test_files_are_cut_at_the_byte_cap(run, monkeypatch):
    monkeypatch.setattr(kd, "FILE_CAP_BYTES", 400)
    result, out = run(cluster())
    assert all(p.stat().st_size <= 400 + 40 for p in out.iterdir())
    assert any("size limit" in w for w in result.warnings)


def test_a_namespace_without_permission_is_a_warning_not_a_failure(run):
    api = cluster()
    api.errors = {("pods", "pm"): ApiException(status=403), ("events", "pm"): ApiException(status=403)}
    result, _ = run(api)
    assert "No permission to read pods in pm." in result.warnings
    assert "No permission to read events in pm." in result.warnings
    assert {ns["name"]: ns["read"] for ns in result.details["namespaces"]} == {"portrait-iq": True, "pm": False}


def test_nothing_readable_fails_with_a_safe_reason(run):
    api = cluster()
    api.errors = {(k, ns): ApiException(status=403) for k in ("pods", "events") for ns in ("portrait-iq", "pm")}
    api.errors[("nodes", None)] = ApiException(status=403)
    with pytest.raises(PluginError) as e:
        run(api)
    assert e.value.safe_reason.startswith("Could not read anything from the Kubernetes API")


def test_no_cluster_access_is_reported(run, monkeypatch):
    def no_access():
        raise PluginError("No Kubernetes access: run Portrait IQ as a pod in the cluster, or set KUBECONFIG_PATH.")
    monkeypatch.setattr(kd, "get_core_api", no_access)
    plugin = KubernetesCollector(LIMITS)
    plugin.bind_job("job-x", __import__("pathlib").Path("/nonexistent"))
    with pytest.raises(PluginError, match="No Kubernetes access"):
        plugin.run("192.0.2.10")


def test_namespace_setting_is_respected(run, monkeypatch):
    monkeypatch.setattr(settings, "k8s_diagnostics_namespaces", "pm, pm ,Bad_Name")
    api = cluster()
    run(api)
    assert {ns for _, ns, _ in api.calls if ns} == {"pm"}          # repeats dropped, invalid names never sent


def test_a_run_shows_the_diagnostics_in_results_and_the_export_zip(engine, tmp_path, monkeypatch):
    """The ticket's 'done when': a run produces the diagnostics in the results and in the zip."""
    from app.packaging import service as packaging
    monkeypatch.setattr(settings, "k8s_diagnostics_namespaces", "")
    monkeypatch.setattr(kd, "get_core_api", lambda: cluster())
    db = sessionmaker(bind=engine)()
    job = create_job(db, "live", ["kubernetes"], LIMITS)
    run_job(db, job, "192.0.2.10")
    result = next(r for r in read_results(job.id) if r["tool"] == "kubernetes")
    assert result["details"]["problem_pods"][0]["name"] == "axonerouter-1"
    assert db.get(Job, job.id).status == "done"

    started = packaging.start(db, job.id, masked=True, requested_by="tester")
    status = packaging.build(db, started.export_id)
    with zipfile.ZipFile(packaging.zip_path(status)) as z:
        names = z.namelist()
        assert any(n.endswith("tools/kubernetes/kubernetes.json") for n in names)
        assert any(n.endswith("tools/kubernetes/describe.txt") for n in names)
        assert any(n.endswith("tools/kubernetes/events.txt") for n in names)
