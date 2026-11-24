"""
SCRUM-105: Grafana / system-health collector.

A read-only snapshot of the platform's own monitoring, taken over Grafana's
HTTP API -- no direct Prometheus access, no internet: Grafana's own health, the
health of its Prometheus data source, node CPU / memory / disk read through
that data source, a dashboard inventory, and any firing alerts.

Every request is bounded by a timeout, and the snapshot is capped in size. The
full JSON is written as a job artifact (packaged in the run zip and covered by
the PHI/PII scan); only counts and levels -- never raw label sets -- reach the
UI summary (SCRUM-68). One failing section is recorded as a note and does not
abort the others; only a Grafana that cannot be reached at all fails the tool.

The backend runs inside the cluster, so it reaches Grafana's service directly;
no tool pod is deployed.
"""
import json

import httpx

from app.collectors.base import Collector, CollectorResult, PluginConfigError, PluginError
from app.config import settings

# node-exporter PromQL, proxied through Grafana's Prometheus data source. A site
# without node-exporter simply returns no data for these, which becomes a note.
NODE_QUERIES = {
    "memory_used_pct":
        "100 * (1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes))",
    "cpu_busy_pct":
        '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))',
    "disk_used_pct":
        '100 * (1 - min(node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs"}'
        " / node_filesystem_size_bytes))",
}
# Warn at or above these (percent).
THRESHOLDS = {"memory_used_pct": 90.0, "cpu_busy_pct": 90.0, "disk_used_pct": 85.0}
_LABELS = {"memory_used_pct": "mem", "cpu_busy_pct": "cpu", "disk_used_pct": "disk"}

# Extra system-health signals, each a scalar read through the same Prometheus
# proxy. kube-state-metrics (node conditions, pod readiness/restarts) and
# node-exporter (load) ship with the kube-prometheus-stack; `up` is Prometheus's
# own target health. `or vector(0)` makes an empty match report 0, not nothing.
HEALTH_QUERIES = {
    "nodes_not_ready":           'sum(kube_node_status_condition{condition="Ready",status="false"}) or vector(0)',
    "nodes_memory_pressure":     'sum(kube_node_status_condition{condition="MemoryPressure",status="true"}) or vector(0)',
    "nodes_disk_pressure":       'sum(kube_node_status_condition{condition="DiskPressure",status="true"}) or vector(0)',
    # Not-ready but not finished: exclude Succeeded pods (completed Job pods are
    # Ready=false but fine) so this counts "should be ready but isn't", not every
    # non-Ready pod -- on a Palette cluster, done import jobs otherwise inflate it.
    "pods_not_ready":            ('count((kube_pod_status_ready{condition="true"} == 0) and on(namespace, pod) '
                                  '(kube_pod_status_phase{phase="Succeeded"} == 0)) or vector(0)'),
    "containers_restarting_15m": 'count(rate(kube_pod_container_status_restarts_total[15m]) > 0) or vector(0)',
    "targets_down":              'count(up == 0) or vector(0)',
    "load1":                     'max(node_load1) or vector(0)',
    "load5":                     'max(node_load5) or vector(0)',
    "load15":                    'max(node_load15) or vector(0)',
}
# Non-zero counts become a warning with this wording.
_HEALTH_WARN = {
    "nodes_not_ready": "{n:.0f} node(s) NotReady",
    "nodes_memory_pressure": "{n:.0f} node(s) under MemoryPressure",
    "nodes_disk_pressure": "{n:.0f} node(s) under DiskPressure",
    "pods_not_ready": "{n:.0f} pod(s) not ready",
    "containers_restarting_15m": "{n:.0f} container(s) restarting in the last 15m",
    "targets_down": "{n:.0f} monitoring target(s) down",
}


class GrafanaClient:
    """Minimal read-only Grafana HTTP API client. A transport can be injected
    for tests (httpx.MockTransport), as the Axone log-export client does."""

    def __init__(self, base_url: str, token: str | None = None, *,
                 timeout: float = 15.0, transport: httpx.BaseTransport | None = None):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers=headers, transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict | None = None):
        r = self._http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def health(self):
        return self._get("/api/health")

    def datasources(self):
        return self._get("/api/datasources")

    def datasource_health(self, uid: str):
        return self._get(f"/api/datasources/uid/{uid}/health")

    def promql(self, uid: str, query: str):
        # Grafana proxies this straight to the data source's Prometheus API.
        return self._get(f"/api/datasources/proxy/uid/{uid}/api/v1/query", params={"query": query})

    def dashboards(self):
        return self._get("/api/search", params={"type": "dash-db", "limit": 1000})

    def prometheus_alerts(self, uid: str):
        # Active alerts from the Prometheus data source's own rules, proxied
        # through Grafana. These are the real kube-prometheus-stack alerts;
        # Grafana-managed unified alerting is usually empty on this platform.
        return self._get(f"/api/datasources/proxy/uid/{uid}/api/v1/alerts")


def _instant_value(prom_response) -> float | None:
    """The scalar from a Prometheus instant-query response, or None."""
    try:
        result = prom_response["data"]["result"]
        return float(result[0]["value"][1]) if result else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _pick_prometheus_uid(datasources) -> str | None:
    if isinstance(datasources, list):
        for ds in datasources:
            if isinstance(ds, dict) and ds.get("type") == "prometheus":
                return ds.get("uid")
    return None


class GrafanaHealthCollector(Collector):
    name = "grafana_health"
    display_name = "Grafana / system health"
    description = ("Read-only snapshot of Grafana, its Prometheus data source, node "
                   "CPU/memory/disk, dashboards and firing alerts.")

    def __init__(self, limits):
        super().__init__(limits)
        self._client: GrafanaClient | None = None

    def configure(self, options):
        if options:
            raise PluginConfigError(f"grafana_health takes no options; got {sorted(options)}")
        if not settings.grafana_base_url:
            raise PluginConfigError("Grafana health can't run: GRAFANA_BASE_URL is not set.")
        self.options = {}

    def _make_client(self) -> GrafanaClient:
        token = settings.grafana_api_token.get_secret_value() if settings.grafana_api_token else None
        return GrafanaClient(settings.grafana_base_url, token, timeout=settings.grafana_timeout_seconds)

    def run(self, target_ip: str) -> CollectorResult:
        out_dir = self.require_output_dir()
        client = self._client = self._make_client()
        notes: list[str] = []   # sections that failed; safe to show (type names only)
        snapshot: dict = {"grafana": None, "datasource": None, "node": {}, "health": {},
                          "dashboards": None, "alerts": None}

        # Grafana itself. If this fails there is nothing to snapshot, so the tool fails.
        try:
            snapshot["grafana"] = client.health()
        except Exception as e:  # noqa: BLE001  one vetted line out; detail stays in logs
            raise PluginError(
                f"grafana: could not reach Grafana at {settings.grafana_base_url} ({type(e).__name__})",
                safe_reason="Could not reach Grafana to collect system health.",
            ) from None

        # Data source: list, pick the Prometheus one, check its health.
        uid = settings.grafana_datasource_uid or None
        try:
            ds = client.datasources()
            uid = uid or _pick_prometheus_uid(ds)
            snapshot["datasource"] = {"count": len(ds) if isinstance(ds, list) else 0, "prometheus_uid": uid}
        except Exception as e:  # noqa: BLE001
            snapshot["datasource"] = {"prometheus_uid": uid}
            notes.append(f"data source list unavailable ({type(e).__name__})")

        ds_ok = None
        firing = None
        if uid:
            try:
                h = client.datasource_health(uid)
                ds_ok = str(h.get("status", "")).upper() == "OK"
                snapshot["datasource"]["health"] = h
            except Exception as e:  # noqa: BLE001
                notes.append(f"data source health unavailable ({type(e).__name__})")
            for key, query in NODE_QUERIES.items():
                try:
                    snapshot["node"][key] = _instant_value(client.promql(uid, query))
                except Exception as e:  # noqa: BLE001
                    snapshot["node"][key] = None
                    notes.append(f"{_LABELS[key]} unavailable ({type(e).__name__})")
            # Firing alerts, from the Prometheus data source's own rules.
            try:
                a = client.prometheus_alerts(uid)
                alerts = (a.get("data", {}) or {}).get("alerts", []) if isinstance(a, dict) else []
                active = [al for al in alerts if str(al.get("state", "")).lower() == "firing"]
                firing = len(active)
                by_sev: dict[str, int] = {}
                for al in active:
                    sev = str(al.get("labels", {}).get("severity", "unknown")).lower()
                    by_sev[sev] = by_sev.get(sev, 0) + 1
                snapshot["alerts"] = {
                    "total": len(alerts), "firing": firing, "by_severity": by_sev,
                    "firing_names": sorted({al.get("labels", {}).get("alertname", "?") for al in active}),
                }
            except Exception as e:  # noqa: BLE001
                notes.append(f"alerts unavailable ({type(e).__name__})")
            # Extra system-health signals (node conditions, crash-looping pods,
            # down targets, load) through the same proxy.
            for key, query in HEALTH_QUERIES.items():
                try:
                    snapshot["health"][key] = _instant_value(client.promql(uid, query))
                except Exception as e:  # noqa: BLE001
                    snapshot["health"][key] = None
                    notes.append(f"{key} unavailable ({type(e).__name__})")
        else:
            notes.append("no Prometheus data source found; node metrics, alerts and health skipped")

        # Dashboard inventory.
        try:
            dash = client.dashboards()
            titles = [d.get("title") for d in dash] if isinstance(dash, list) else []
            snapshot["dashboards"] = {"count": len(titles), "titles": titles[:200]}
        except Exception as e:  # noqa: BLE001
            notes.append(f"dashboard inventory unavailable ({type(e).__name__})")

        # Write the artifact, size-capped (also bounded by the job's output limit).
        cap = min(self.limits.max_output_mb, settings.grafana_max_snapshot_mb) * 1024 * 1024
        artifact = out_dir / "grafana_health.json"

        def _dump(obj) -> str:
            return json.dumps(obj, indent=2, default=str)

        def _too_big(text: str) -> bool:
            return len(text.encode()) > cap

        blob = _dump(snapshot)
        if _too_big(blob):
            # Trim a copy from the bulkiest, least essential section inward,
            # RE-MEASURING after every cut, so the file written is always within
            # the cap -- a large alerts/health section can't slip through just
            # because the dashboard list was dropped. `snapshot` itself is left
            # whole for the summary/warnings (which carry counts only, no bulk).
            trimmed = {k: (dict(v) if isinstance(v, dict) else v) for k, v in snapshot.items()}
            dropped: list[str] = []
            for section, field, label in (("dashboards", "titles", "dashboard titles"),
                                          ("alerts", "firing_names", "firing alert names")):
                if not _too_big(blob):
                    break
                sec = trimmed.get(section)
                if isinstance(sec, dict) and sec.get(field):
                    sec[field] = []
                    dropped.append(label)
                    blob = _dump({**trimmed, "truncated": dropped})
            if _too_big(blob):
                # Still over cap without the lists: keep the scalar health only.
                trimmed = {k: snapshot.get(k) for k in ("grafana", "datasource", "node", "health")}
                dropped.append("all lists")
                blob = _dump({**trimmed, "dashboards": None, "alerts": None, "truncated": dropped})
            notes.append("snapshot exceeded the size cap; trimmed: " + ", ".join(dropped))
        artifact.write_text(blob)

        return CollectorResult(
            tool=self.name,
            artifact_paths=[str(artifact)],
            summary=self._summary(snapshot, ds_ok, firing),
            warnings=self._warnings(snapshot, ds_ok, firing) + notes,
        )

    # -- summary / warnings: counts and levels only, never raw labels ----------

    def _summary(self, snapshot, ds_ok, firing) -> str:
        parts = ["Grafana up"]
        if ds_ok is True:
            parts.append("Prometheus ok")
        elif ds_ok is False:
            parts.append("Prometheus DOWN")
        for key, value in snapshot["node"].items():
            if value is not None:
                parts.append(f"{_LABELS[key]} {value:.0f}%")
        load1 = (snapshot.get("health") or {}).get("load1")
        if load1 is not None:
            parts.append(f"load {load1:.1f}")
        if snapshot["dashboards"]:
            parts.append(f"{snapshot['dashboards']['count']} dashboards")
        if firing is not None:
            parts.append(f"{firing} alerts firing")
        return ", ".join(parts)

    def _warnings(self, snapshot, ds_ok, firing) -> list[str]:
        warnings = []
        if ds_ok is False:
            warnings.append("Grafana's Prometheus data source reports unhealthy.")
        for key, value in snapshot["node"].items():
            if value is not None and value >= THRESHOLDS[key]:
                warnings.append(f"Node {_LABELS[key]} at {value:.0f}% (threshold {THRESHOLDS[key]:.0f}%).")
        health = snapshot.get("health") or {}
        for key, msg in _HEALTH_WARN.items():
            n = health.get(key)
            if n:   # non-zero and not None
                warnings.append(msg.format(n=n) + ".")
        if firing:
            by_sev = (snapshot.get("alerts") or {}).get("by_severity", {})
            sev = ", ".join(f"{n} {s}" for s, n in sorted(by_sev.items())) or "unknown severity"
            warnings.append(f"{firing} Prometheus alert(s) firing: {sev}.")
        return warnings

    def cleanup(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
