"""
SCRUM-105: the Grafana / system-health collector. Grafana's HTTP API is faked
with an httpx.MockTransport; all values below are synthetic.
"""
import json

import httpx
import pytest

from app.collectors import grafana
from app.collectors.base import PluginConfigError, PluginError, ResourceLimits
from app.collectors.grafana import GrafanaClient, GrafanaHealthCollector
from app.config import settings

LIMITS = ResourceLimits(cpu_pct=50, mem_mb=512, max_output_mb=100)

# (marker in query) -> value returned, first match wins. Order matters:
# node_load15 must precede node_load1 (substring). Health-count queries aren't
# listed here, so they default to "0" — a healthy node.
QUERY_VALUES = [
    ("MemAvailable", "62.0"), ("node_cpu", "22.0"), ("node_filesystem", "40.0"),
    ("node_load15", "0.9"), ("node_load5", "1.1"), ("node_load1", "1.3"),
]


def grafana_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/health":
        return httpx.Response(200, json={"database": "ok", "version": "10.2.3"})
    if path == "/api/datasources":
        return httpx.Response(200, json=[
            {"uid": "prom1", "type": "prometheus", "name": "Prometheus"},
            {"uid": "loki1", "type": "loki", "name": "Loki"},
        ])
    if path == "/api/datasources/uid/prom1/health":
        return httpx.Response(200, json={"status": "OK", "message": "Data source is working"})
    if path == "/api/datasources/proxy/uid/prom1/api/v1/alerts":
        return httpx.Response(200, json={"status": "success", "data": {"alerts": [
            {"labels": {"alertname": "HighMemory", "severity": "warning"}, "state": "firing"},
            {"labels": {"alertname": "Quiet", "severity": "info"}, "state": "normal"},
        ]}})
    if path.startswith("/api/datasources/proxy/uid/prom1/api/v1/query"):
        q = request.url.params.get("query", "")
        val = next((v for marker, v in QUERY_VALUES if marker in q), "0")
        return httpx.Response(200, json={"status": "success",
            "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1700000000, val]}]}})
    if path == "/api/search":
        return httpx.Response(200, json=[{"title": "Node Exporter Full"}, {"title": "Kubernetes"}])
    return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "grafana_base_url", "http://grafana.test.svc:3000")
    monkeypatch.setattr(settings, "grafana_api_token", None)
    monkeypatch.setattr(settings, "grafana_datasource_uid", "")


def configured(tmp_path, monkeypatch, handler=grafana_handler) -> GrafanaHealthCollector:
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(GrafanaHealthCollector, "_make_client",
                        lambda self: GrafanaClient(settings.grafana_base_url, None, transport=transport))
    c = GrafanaHealthCollector(LIMITS)
    c.configure({})
    c.bind_job("job-1", tmp_path)
    return c


def test_off_unless_configured():
    with pytest.raises(PluginConfigError, match="GRAFANA_BASE_URL is not set"):
        GrafanaHealthCollector(LIMITS).configure({})


def test_token_loads_from_a_secret_file(monkeypatch, tmp_path):
    # The Viewer token is a mounted secret file in the pod, like the login
    # secrets (SCRUM-105 deploy wiring): GRAFANA_API_TOKEN_FILE -> setting.
    from app.config import Settings, load_login_secret_files
    path = tmp_path / "GRAFANA_API_TOKEN"
    path.write_text("glsa_testtoken")
    path.chmod(0o400)
    monkeypatch.setenv("GRAFANA_API_TOKEN_FILE", str(path))
    s = Settings()
    assert s.grafana_api_token is None
    load_login_secret_files(s)
    assert s.grafana_api_token is not None
    assert s.grafana_api_token.get_secret_value() == "glsa_testtoken"


def test_takes_no_options(enabled):
    with pytest.raises(PluginConfigError, match="takes no options"):
        GrafanaHealthCollector(LIMITS).configure({"window": "5m"})


def test_snapshot_summarizes_health(tmp_path, monkeypatch, enabled):
    result = configured(tmp_path, monkeypatch).run("192.0.2.3")

    assert result.summary == ("Grafana up, Prometheus ok, mem 62%, cpu 22%, disk 40%, "
                              "load 1.3, 2 dashboards, 1 alerts firing")
    # Healthy node: the only warning is the firing alert, no health-count warnings.
    assert result.warnings == ["1 Prometheus alert(s) firing: 1 warning."]

    saved = json.loads((tmp_path / "grafana_health.json").read_text())
    assert saved["grafana"]["database"] == "ok"
    assert saved["datasource"]["prometheus_uid"] == "prom1"
    assert saved["node"] == {"memory_used_pct": 62.0, "cpu_busy_pct": 22.0, "disk_used_pct": 40.0}
    assert saved["health"] == {
        "nodes_not_ready": 0.0, "nodes_memory_pressure": 0.0, "nodes_disk_pressure": 0.0,
        "pods_not_ready": 0.0, "containers_restarting_15m": 0.0, "targets_down": 0.0,
        "load1": 1.3, "load5": 1.1, "load15": 0.9}
    assert saved["dashboards"]["count"] == 2
    assert saved["alerts"] == {
        "total": 2, "firing": 1, "by_severity": {"warning": 1}, "firing_names": ["HighMemory"]}


def test_system_health_signals_warn(tmp_path, monkeypatch, enabled):
    def unhealthy(request):
        if request.url.path.startswith("/api/datasources/proxy/uid/prom1/api/v1/query"):
            q = request.url.params.get("query", "")
            vals = [("MemoryPressure", "1"), ("kube_pod_status_ready", "3"),
                    ("kube_pod_container_status_restarts_total", "2"), ("up == 0", "1")]
            v = next((x for m, x in vals if m in q), "0")
            return httpx.Response(200, json={"status": "success",
                "data": {"result": [{"metric": {}, "value": [1, v]}]}})
        return grafana_handler(request)
    warnings = configured(tmp_path, monkeypatch, handler=unhealthy).run("192.0.2.3").warnings
    joined = " | ".join(warnings)
    assert "1 node(s) under MemoryPressure" in joined
    assert "3 pod(s) not ready" in joined
    assert "2 container(s) restarting in the last 15m" in joined
    assert "1 monitoring target(s) down" in joined


def test_unreachable_grafana_is_a_plugin_error_and_cleanup_closes(tmp_path, monkeypatch, enabled):
    def down(request):
        return httpx.Response(503, json={"message": "unavailable"})
    c = configured(tmp_path, monkeypatch, handler=down)
    with pytest.raises(PluginError, match="could not reach Grafana"):
        c.run("192.0.2.3")
    c.cleanup()   # must not raise


def test_high_usage_warns(tmp_path, monkeypatch, enabled):
    def hot(request):
        if request.url.path.startswith("/api/datasources/proxy/uid/prom1/api/v1/query"):
            q = request.url.params.get("query", "")
            val = "96.0" if "MemAvailable" in q else "10.0"
            return httpx.Response(200, json={"status": "success",
                "data": {"result": [{"metric": {}, "value": [1700000000, val]}]}})
        return grafana_handler(request)
    result = configured(tmp_path, monkeypatch, handler=hot).run("192.0.2.3")
    assert any("mem at 96%" in w for w in result.warnings)


def test_no_prometheus_datasource_skips_node_but_still_snapshots(tmp_path, monkeypatch, enabled):
    def no_prom(request):
        if request.url.path == "/api/datasources":
            return httpx.Response(200, json=[{"uid": "loki1", "type": "loki", "name": "Loki"}])
        return grafana_handler(request)
    result = configured(tmp_path, monkeypatch, handler=no_prom).run("192.0.2.3")
    assert result.summary.startswith("Grafana up")
    assert "mem" not in result.summary
    assert any("no Prometheus data source found" in w for w in result.warnings)


def test_one_failing_section_is_a_note_not_fatal(tmp_path, monkeypatch, enabled):
    def no_search(request):
        if request.url.path == "/api/search":
            return httpx.Response(500, json={"message": "boom"})
        return grafana_handler(request)
    result = configured(tmp_path, monkeypatch, handler=no_search).run("192.0.2.3")
    assert result.summary.startswith("Grafana up, Prometheus ok")   # run still succeeded
    assert any("dashboard inventory unavailable" in w for w in result.warnings)


def test_oversize_snapshot_is_trimmed_until_within_cap(tmp_path, monkeypatch, enabled):
    # A bulky non-dashboard section (here the firing-alert names) must not slip
    # through just because the dashboard list was dropped: the artifact is
    # re-measured after each cut, so the file written is always within the cap
    # (allen #5). The old code dropped only `dashboards` and wrote without
    # re-measuring, which this would have caught.
    monkeypatch.setattr(settings, "grafana_max_snapshot_mb", 1)
    names = [f"alertname-{n:08d}-padding" for n in range(40000)]   # > 1 MB of names alone

    def bulky(request):
        if request.url.path == "/api/datasources/proxy/uid/prom1/api/v1/alerts":
            alerts = [{"labels": {"alertname": nm, "severity": "warning"}, "state": "firing"}
                      for nm in names]
            return httpx.Response(200, json={"status": "success", "data": {"alerts": alerts}})
        return grafana_handler(request)

    configured(tmp_path, monkeypatch, handler=bulky).run("192.0.2.3")

    text = (tmp_path / "grafana_health.json").read_text()
    assert len(text.encode()) <= 1 * 1024 * 1024                  # the written file respects the cap
    saved = json.loads(text)
    assert "firing alert names" in saved.get("truncated", [])     # the bulky section was the one cut
    assert saved["alerts"]["firing"] == len(names)                # counts survive the trim
    assert saved["alerts"]["firing_names"] == []
