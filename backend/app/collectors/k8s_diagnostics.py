"""
Kubernetes diagnostics: a read-only snapshot of the cluster for troubleshooting.

Collects node status, pod status with restart counts and the last restart
reason, events, and `kubectl describe`-style text, for the namespaces in
settings.k8s_diagnostics_namespaces (ours and Portrait's pm by default)
plus the nodes. Nothing is changed and no pod is started: only list calls on
the Kubernetes API, with the backend's own service account (read-only RBAC in
deploy/k8s/tool-rbac.yaml and deploy/k8s-portrait/).

Never collected: container environment variables, command lines and
arguments, Secrets, ConfigMaps, annotations and pod logs. Those can hold
credentials or patient data. Free text the cluster writes (event messages,
pod and condition messages) goes through the PHI/PII scanner first, and
personal data is masked like on the Logs page.

Size-capped: at most MAX_PODS pods and MAX_EVENTS events per namespace, a
cut per describe section, and a byte cap per file. Anything dropped is named
in a warning. A namespace that can't be read (no permission, not found) is a
warning, not a failure: the tool fails only when nothing at all could be read.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from kubernetes.client.exceptions import ApiException

from app.collectors import k8s
from app.collectors.base import Collector, CollectorResult, PluginError
from app.config import diagnostics_namespaces
from app.privacy.scanner import category_group, mask_text, scan_text

MAX_NODES = 50
MAX_PODS = 500              # per namespace
EVENT_FETCH = 2000          # per namespace, read from the API; the newest MAX_EVENTS are kept
MAX_EVENTS = 500
DESCRIBE_SECTION_CHARS = 8000
FILE_CAP_BYTES = 20 * 1024 * 1024
DETAIL_ROWS = 50            # rows per table sent to the results page
WARNING_ROWS = 10

_PRESSURE = ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable")
_NAMESPACE_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def get_core_api():
    """The Kubernetes API client (in-cluster service account or KUBECONFIG_PATH). Tests replace this."""
    return k8s._load_core_api()


# --- small helpers -------------------------------------------------------------------------

def _get(obj: Any, path: str, default=None):
    """obj.a.b.c without failing on a missing step (API objects leave fields as None)."""
    for part in path.split("."):
        if obj is None:
            return default
        obj = getattr(obj, part, None)
    return default if obj is None else obj


def _iso(ts) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    return str(ts)


def _safe(text: Optional[str], limit: int = 500) -> Optional[str]:
    """Cluster-written free text with personal data masked, like the Logs page."""
    if not text:
        return text
    text = str(text)[:limit]
    flags = [f for f in scan_text(text) if category_group(f.category) == "personal"]
    return mask_text(text, flags) if flags else text


def _qty(mapping, key) -> Optional[str]:
    if not mapping:
        return None
    value = mapping.get(key) if isinstance(mapping, dict) else getattr(mapping, key, None)
    return None if value is None else str(value)


class _Skipped(Exception):
    """One API read that failed; the message is safe to show."""


def _call(what: str, namespace: Optional[str], fn, *args, **kwargs):
    where = f" in {namespace}" if namespace else ""
    try:
        return fn(*args, _request_timeout=k8s.API_TIMEOUT_S, **kwargs)
    except ApiException as e:
        if e.status == 403:
            raise _Skipped(f"No permission to read {what}{where}.") from None
        if e.status == 404:
            raise _Skipped(f"Namespace {namespace} was not found." if namespace else f"Could not find {what}.") from None
        raise _Skipped(f"Could not read {what}{where} (HTTP {e.status}).") from None
    except PluginError:
        raise
    except Exception as e:  # noqa: BLE001  network trouble: name the type only
        raise _Skipped(f"Could not reach the Kubernetes API to read {what}{where} ({type(e).__name__}).") from None


def _truncated(result) -> bool:
    return bool(_get(result, "metadata._continue"))


# --- reading the cluster -------------------------------------------------------------------

def _node(n) -> dict:
    conditions = {c.type: c for c in (_get(n, "status.conditions") or [])}
    ready = conditions.get("Ready")
    labels = _get(n, "metadata.labels") or {}
    return {
        "name": _get(n, "metadata.name"),
        "ready": bool(ready and ready.status == "True"),
        "ready_reason": _get(ready, "reason"),
        "pressure": [t for t in _PRESSURE if _get(conditions.get(t), "status") == "True"],
        "roles": sorted(k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/")) or ["none"],
        "kubelet_version": _get(n, "status.node_info.kubelet_version"),
        "os_image": _get(n, "status.node_info.os_image"),
        "container_runtime": _get(n, "status.node_info.container_runtime_version"),
        "cpu_capacity": _qty(_get(n, "status.capacity"), "cpu"),
        "memory_capacity": _qty(_get(n, "status.capacity"), "memory"),
        "cpu_allocatable": _qty(_get(n, "status.allocatable"), "cpu"),
        "memory_allocatable": _qty(_get(n, "status.allocatable"), "memory"),
        "created": _iso(_get(n, "metadata.creation_timestamp")),
        "conditions": [{"type": c.type, "status": c.status, "reason": _get(c, "reason"),
                        "message": _safe(_get(c, "message"))} for c in conditions.values()],
    }


def _container(spec, status) -> dict:
    state = _get(status, "state")
    current = ("running" if _get(state, "running") else
               "waiting" if _get(state, "waiting") else
               "terminated" if _get(state, "terminated") else "unknown")
    last = _get(status, "last_state.terminated")
    resources = _get(spec, "resources")
    return {
        "name": _get(spec, "name") or _get(status, "name"),
        "image": _get(status, "image") or _get(spec, "image"),
        "state": current,
        "state_reason": _get(state, "waiting.reason") or _get(state, "terminated.reason"),
        "ready": bool(_get(status, "ready", False)),
        "restarts": int(_get(status, "restart_count", 0) or 0),
        "last_reason": _get(last, "reason"),
        "last_exit_code": _get(last, "exit_code"),
        "last_finished": _iso(_get(last, "finished_at")),
        "requests": {k: str(v) for k, v in (_get(resources, "requests") or {}).items()},
        "limits": {k: str(v) for k, v in (_get(resources, "limits") or {}).items()},
    }


def _pod(p) -> dict:
    statuses = {s.name: s for s in (_get(p, "status.container_statuses") or [])}
    containers = [_container(c, statuses.get(c.name)) for c in (_get(p, "spec.containers") or [])]
    conditions = {c.type: c.status for c in (_get(p, "status.conditions") or [])}
    return {
        "namespace": _get(p, "metadata.namespace"),
        "name": _get(p, "metadata.name"),
        "phase": _get(p, "status.phase", "Unknown"),
        "reason": _get(p, "status.reason"),
        "message": _safe(_get(p, "status.message")),
        "node": _get(p, "spec.node_name"),
        "created": _iso(_get(p, "metadata.creation_timestamp")),
        "started": _iso(_get(p, "status.start_time")),
        "ready": conditions.get("Ready") == "True",
        "ready_count": f"{sum(c['ready'] for c in containers)}/{len(containers)}",
        "restarts": sum(c["restarts"] for c in containers),
        "labels": dict(_get(p, "metadata.labels") or {}),
        "containers": containers,
    }


def _event_time(e):
    return (_get(e, "last_timestamp") or _get(e, "event_time") or _get(e, "first_timestamp")
            or _get(e, "metadata.creation_timestamp"))


def _event(e) -> dict:
    involved = _get(e, "involved_object")
    return {
        "namespace": _get(e, "metadata.namespace"),
        "type": _get(e, "type", "Normal"),
        "reason": _get(e, "reason"),
        "object": f"{_get(involved, 'kind', '?')}/{_get(involved, 'name', '?')}",
        "count": int(_get(e, "count", 1) or 1),
        "last_seen": _iso(_event_time(e)),
        "message": _safe(_get(e, "message")),
    }


# --- text output ---------------------------------------------------------------------------

def _cut(text: str, limit: int = DESCRIBE_SECTION_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n  … (cut: section size limit)\n"


def _describe_node(n: dict) -> str:
    lines = [f"Name:               {n['name']}", f"Roles:              {', '.join(n['roles'])}",
             f"Created:            {n['created']}", f"Ready:              {n['ready']} ({n['ready_reason'] or '-'})",
             f"Pressure:           {', '.join(n['pressure']) or 'none'}",
             f"Kubelet version:    {n['kubelet_version']}", f"OS image:           {n['os_image']}",
             f"Container runtime:  {n['container_runtime']}",
             f"Capacity:           cpu={n['cpu_capacity']} memory={n['memory_capacity']}",
             f"Allocatable:        cpu={n['cpu_allocatable']} memory={n['memory_allocatable']}", "Conditions:"]
    lines += [f"  {c['type']:<20} {c['status']:<6} {c['reason'] or ''} {c['message'] or ''}".rstrip()
              for c in n["conditions"]]
    return _cut("\n".join(lines) + "\n")


def _describe_pod(p: dict, events: list[dict]) -> str:
    lines = [f"Name:         {p['name']}", f"Namespace:    {p['namespace']}", f"Node:         {p['node']}",
             f"Created:      {p['created']}", f"Status:       {p['phase']}" + (f" ({p['reason']})" if p["reason"] else ""),
             f"Ready:        {p['ready_count']}", f"Restarts:     {p['restarts']}"]
    if p["message"]:
        lines.append(f"Message:      {p['message']}")
    if p["labels"]:
        lines.append("Labels:       " + ", ".join(f"{k}={v}" for k, v in sorted(p["labels"].items())))
    lines.append("Containers:")
    for c in p["containers"]:
        lines += [f"  {c['name']}:", f"    Image:          {c['image']}",
                  f"    State:          {c['state']}" + (f" ({c['state_reason']})" if c["state_reason"] else ""),
                  f"    Ready:          {c['ready']}", f"    Restart count:  {c['restarts']}"]
        if c["last_reason"]:
            lines.append(f"    Last state:     terminated ({c['last_reason']}, exit code {c['last_exit_code']}, "
                         f"finished {c['last_finished']})")
        if c["requests"] or c["limits"]:
            lines.append(f"    Requests:       {c['requests'] or '-'}  Limits: {c['limits'] or '-'}")
    mine = [e for e in events if e["object"].endswith(f"/{p['name']}")]
    lines.append("Events:" + ("" if mine else " none kept for this pod"))
    lines += [f"  {e['type']:<8} {e['reason'] or '':<22} x{e['count']:<4} {e['last_seen']}  {e['message'] or ''}"
              for e in mine[:20]]
    return _cut("\n".join(lines) + "\n")


def _events_text(events_by_ns: dict) -> str:
    out = []
    for ns, events in events_by_ns.items():
        out.append(f"== Events in {ns}, newest first ({len(events)} kept)")
        out += [f"{e['last_seen']}  {e['type']:<8} {e['reason'] or '':<22} x{e['count']:<4} {e['object']}  "
                f"{e['message'] or ''}" for e in events]
        out.append("")
    return "\n".join(out)


def _write_capped(path, text: str, cap: int, warnings: list[str]) -> None:
    data = text.encode("utf-8")
    if len(data) > cap:
        data = data[:cap] + b"\n... (cut: file size limit)\n"
        warnings.append(f"{path.name} was cut at the {cap // (1024 * 1024)} MB size limit.")
    path.write_bytes(data)


# --- the plugin ----------------------------------------------------------------------------

class KubernetesCollector(Collector):
    name = "kubernetes"
    display_name = "Kubernetes diagnostics"
    description = ("Read-only snapshot of the cluster: node and pod status, restart counts, "
                   "events and describe output.")

    def run(self, target_ip: str) -> CollectorResult:
        out_dir = self.require_output_dir()
        namespaces = [ns for ns in diagnostics_namespaces() if _NAMESPACE_NAME.match(ns)]
        api = get_core_api()                 # PluginError (safe) when there is no cluster access
        notes: list[str] = []
        reads_ok = 0

        nodes: list[dict] = []
        try:
            result = _call("nodes", None, api.list_node, limit=MAX_NODES)
            nodes = [_node(n) for n in result.items]
            reads_ok += 1
            if _truncated(result):
                notes.append(f"Only the first {MAX_NODES} nodes were read.")
        except _Skipped as e:
            notes.append(str(e))

        pods: dict[str, list[dict]] = {}
        events: dict[str, list[dict]] = {}
        for ns in namespaces:
            try:
                result = _call("pods", ns, api.list_namespaced_pod, ns, limit=MAX_PODS)
                pods[ns] = [_pod(p) for p in result.items]
                reads_ok += 1
                if _truncated(result):
                    notes.append(f"Only the first {MAX_PODS} pods in {ns} were read.")
            except _Skipped as e:
                notes.append(str(e))
            try:
                result = _call("events", ns, api.list_namespaced_event, ns, limit=EVENT_FETCH)
                found = sorted((_event(e) for e in result.items), key=lambda e: e["last_seen"] or "", reverse=True)
                reads_ok += 1
                if len(found) > MAX_EVENTS or _truncated(result):
                    notes.append(f"Only the newest {min(len(found), MAX_EVENTS)} events in {ns} were kept.")
                events[ns] = found[:MAX_EVENTS]
            except _Skipped as e:
                notes.append(str(e))

        if not reads_ok:
            raise PluginError(
                "Kubernetes diagnostics could not read anything: " + " ".join(notes),
                safe_reason="Could not read anything from the Kubernetes API. Check the app's access "
                            "(deploy/k8s/tool-rbac.yaml) and that the cluster is up.",
            )

        all_pods = [p for ns in pods.values() for p in ns]
        all_events = [e for ns in events.values() for e in ns]
        warnings = self._findings(nodes, all_pods, all_events) + notes
        cap = min(FILE_CAP_BYTES, max(1, self.limits.max_output_mb) * 1024 * 1024 // 3)

        snapshot = {"collected_at": _iso(datetime.now(timezone.utc)), "namespaces": namespaces,
                    "nodes": nodes, "pods": pods, "events": events, "notes": notes}
        describe = "".join(f"===== Node {n['name']}\n{_describe_node(n)}\n" for n in nodes)
        describe += "".join(f"===== Pod {p['namespace']}/{p['name']}\n"
                            f"{_describe_pod(p, events.get(p['namespace'], []))}\n" for p in all_pods)
        files = {"kubernetes.json": json.dumps(snapshot, indent=2, default=str),
                 "describe.txt": describe, "events.txt": _events_text(events)}
        for name, text in files.items():
            _write_capped(out_dir / name, text, cap, warnings)

        return CollectorResult(
            tool=self.name,
            artifact_paths=[str(out_dir / name) for name in files],
            summary=self._summary(nodes, pods, all_pods, all_events),
            warnings=warnings,
            details=self._details(namespaces, nodes, pods, events, notes),
        )

    def cleanup(self) -> None:
        """Nothing to tear down: run() only reads."""

    # -- result shaping --------------------------------------------------------

    @staticmethod
    def _findings(nodes, pods, events) -> list[str]:
        out = []
        for n in nodes:
            if not n["ready"]:
                out.append(f"Node {n['name']} is not Ready ({n['ready_reason'] or 'no reason given'}).")
            if n["pressure"]:
                out.append(f"Node {n['name']} reports {', '.join(n['pressure'])}.")
        restarted = sorted((p for p in pods if p["restarts"]), key=lambda p: -p["restarts"])
        for p in restarted[:WARNING_ROWS]:
            last = next((c["last_reason"] for c in p["containers"] if c["last_reason"]), None)
            out.append(f"{p['namespace']}/{p['name']} restarted {p['restarts']} time{'s' if p['restarts'] != 1 else ''}"
                       + (f", last: {last}" if last else "") + ".")
        if len(restarted) > WARNING_ROWS:
            out.append(f"{len(restarted) - WARNING_ROWS} more pods have restarted (see the results page).")
        not_ready = [p for p in pods if not p["ready"] and p["phase"] not in ("Succeeded",)]
        if not_ready:
            out.append(f"{len(not_ready)} pod{'s are' if len(not_ready) != 1 else ' is'} not Ready.")
        warn = [e for e in events if e["type"] == "Warning"]
        if warn:
            out.append(f"{len(warn)} warning event{'s' if len(warn) != 1 else ''} (newest first on the results page).")
        return out

    @staticmethod
    def _summary(nodes, pods_by_ns, pods, events) -> str:
        ready = sum(n["ready"] for n in nodes)
        restarted = sum(1 for p in pods if p["restarts"])
        warn = sum(1 for e in events if e["type"] == "Warning")
        return (f"{ready} of {len(nodes)} node{'s' if len(nodes) != 1 else ''} Ready; {len(pods)} pods in "
                f"{len(pods_by_ns)} namespace{'s' if len(pods_by_ns) != 1 else ''}, {restarted} restarted; "
                f"{warn} warning event{'s' if warn != 1 else ''}")

    @staticmethod
    def _details(namespaces, nodes, pods, events, notes) -> dict:
        all_pods = [p for ns in pods.values() for p in ns]
        problem = [p for p in all_pods if p["restarts"] or (not p["ready"] and p["phase"] != "Succeeded")]
        problem.sort(key=lambda p: (-p["restarts"], p["namespace"], p["name"]))

        def last(p, field):
            return next((c[field] for c in p["containers"] if c["last_reason"]), None)

        warn = sorted((e for ns in events.values() for e in ns if e["type"] == "Warning"),
                      key=lambda e: e["last_seen"] or "", reverse=True)
        return {
            "namespaces": [{
                "name": ns,
                "read": ns in pods,
                "pods": len(pods.get(ns, [])),
                "not_ready": sum(1 for p in pods.get(ns, []) if not p["ready"] and p["phase"] != "Succeeded"),
                "restarted": sum(1 for p in pods.get(ns, []) if p["restarts"]),
                "warning_events": sum(1 for e in events.get(ns, []) if e["type"] == "Warning"),
            } for ns in namespaces],
            "nodes": [{k: n[k] for k in ("name", "ready", "ready_reason", "pressure", "roles", "kubelet_version",
                                          "cpu_capacity", "memory_capacity")} for n in nodes[:DETAIL_ROWS]],
            "problem_pods": [{
                "namespace": p["namespace"], "name": p["name"], "phase": p["phase"], "ready": p["ready_count"],
                "restarts": p["restarts"], "last_reason": last(p, "last_reason"),
                "last_exit_code": last(p, "last_exit_code"), "last_finished": last(p, "last_finished"),
                "node": p["node"],
            } for p in problem[:DETAIL_ROWS]],
            "problem_pods_total": len(problem),
            "warning_events": [{k: e[k] for k in ("namespace", "object", "reason", "count", "last_seen", "message")}
                               for e in warn[:DETAIL_ROWS]],
            "warning_events_total": len(warn),
            "notes": notes,
        }
