import errno
import json
import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import User
from app.connectivity import service
from app.db import get_db
from app.orchestrator import routes
from app.schemas import (
    ConnectivityCheckRequest,
    ConnectivityCheckResponse,
    ConnectivityPortResult,
)


def _endpoint() -> service.ResolvedEndpoint:
    return service.ResolvedEndpoint(socket.AF_INET, socket.SOCK_STREAM, 6, ("192.0.2.10", 0))


def test_reachable_port(monkeypatch):
    monkeypatch.setattr(service, "_resolve", lambda target: [_endpoint()])
    monkeypatch.setattr(service, "_connect", lambda *args: 0)

    result = service.check_connectivity("example.test", [443], 1.0)

    assert result.status == "reachable"
    assert result.reachable is True
    assert result.resolved_addresses == ["192.0.2.10"]
    assert result.results[0].status == "reachable"


@pytest.mark.parametrize(
    ("error_number", "expected_status"),
    [
        (errno.ECONNREFUSED, "connection_refused"),
        (errno.ETIMEDOUT, "timeout"),
        (errno.EHOSTUNREACH, "host_unreachable"),
        (errno.ENETUNREACH, "network_unreachable"),
        (errno.EACCES, "blocked_by_local_policy"),
        (errno.EIO, "connection_error"),
    ],
)
def test_socket_failure_is_classified(monkeypatch, error_number, expected_status):
    monkeypatch.setattr(service, "_resolve", lambda target: [_endpoint()])
    monkeypatch.setattr(service, "_connect", lambda *args: error_number)

    result = service.check_connectivity("example.test", [443], 1.0)

    assert result.status == "unreachable"
    assert result.reachable is False
    assert result.results[0].status == expected_status


def test_dns_failure_is_reported_for_each_port(monkeypatch):
    def fail_resolution(target):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(service, "_resolve", fail_resolution)

    result = service.check_connectivity("missing.test", [22, 443], 1.0)

    assert result.status == "unreachable"
    assert result.resolved_addresses == []
    assert [item.status for item in result.results] == ["dns_failure", "dns_failure"]


def test_value_error_during_dns_is_reported(monkeypatch):
    monkeypatch.setattr(
        service,
        "_resolve",
        lambda target: (_ for _ in ()).throw(ValueError("null byte")),
    )

    result = service.check_connectivity("bad\0target", [443], 1.0)

    assert result.results[0].status == "dns_failure"


def test_dns_timeout_is_reported_and_future_is_cancelled(monkeypatch):
    class TimedOutFuture:
        cancelled = False

        def result(self, timeout):
            raise service.FutureTimeoutError

        def cancel(self):
            self.cancelled = True

    future = TimedOutFuture()

    class Executor:
        def submit(self, function, target):
            return future

    monkeypatch.setattr(service, "_DNS_EXECUTOR", Executor())

    result = service.check_connectivity("slow.test", [443], 0.1)

    assert result.results[0].status == "dns_failure"
    assert future.cancelled is True


def test_no_resolved_endpoints_is_dns_failure(monkeypatch):
    monkeypatch.setattr(service, "_resolve", lambda target: [])

    result = service.check_connectivity("example.test", [443], 1.0)

    assert result.results[0].status == "dns_failure"


def test_resolve_deduplicates_addresses(monkeypatch):
    answer = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 0))
    monkeypatch.setattr(service.socket, "getaddrinfo", lambda *args, **kwargs: [answer, answer])

    assert service._resolve("example.test") == [_endpoint()]


def test_ipv6_endpoint_replaces_port():
    endpoint = service.ResolvedEndpoint(
        socket.AF_INET6, socket.SOCK_STREAM, 6, ("2001:db8::1", 0, 7, 8)
    )

    assert endpoint.for_port(443) == ("2001:db8::1", 443, 7, 8)


def test_later_ipv4_address_is_attempted_after_ipv6_timeout(monkeypatch):
    ipv6 = service.ResolvedEndpoint(
        socket.AF_INET6, socket.SOCK_STREAM, 6, ("2001:db8::1", 0, 0, 0)
    )
    ipv4 = _endpoint()
    calls = []

    def connect(endpoint, port, timeout_seconds):
        calls.append((endpoint.family, timeout_seconds))
        if endpoint.family == socket.AF_INET6:
            raise socket.timeout
        return errno.ECONNREFUSED

    monkeypatch.setattr(service, "_resolve", lambda target: [ipv6, ipv4])
    monkeypatch.setattr(service, "_connect", connect)

    result = service.check_connectivity("example.test", [443], 1.0)

    assert [family for family, _ in calls] == [socket.AF_INET6, socket.AF_INET]
    assert calls[0][1] <= 0.5
    assert result.results[0].status == "connection_refused"


@pytest.mark.parametrize(
    ("exception", "expected_status"),
    [(socket.timeout(), "timeout"), (OSError(errno.ENETUNREACH, "no route"), "network_unreachable")],
)
def test_connector_exceptions_are_classified(monkeypatch, exception, expected_status):
    monkeypatch.setattr(service, "_resolve", lambda target: [_endpoint()])

    def fail(*args):
        raise exception

    monkeypatch.setattr(service, "_connect", fail)

    result = service.check_connectivity("example.test", [443], 1.0)

    assert result.results[0].status == expected_status


def test_real_loopback_socket_reports_open_and_closed_ports():
    endpoint = service.ResolvedEndpoint(
        socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, ("127.0.0.1", 0)
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", 0))
        except PermissionError:
            pytest.skip("loopback sockets are disabled by the test sandbox")
        listener.listen()
        open_port = listener.getsockname()[1]

        assert service._connect(endpoint, open_port, 1.0) == 0

    assert service._connect(endpoint, open_port, 1.0) == errno.ECONNREFUSED


def test_partial_reachability_preserves_requested_port_order(monkeypatch):
    monkeypatch.setattr(service, "_resolve", lambda target: [_endpoint()])
    monkeypatch.setattr(
        service,
        "_connect",
        lambda endpoint, port, timeout: {443: 0, 22: errno.ECONNREFUSED}[port],
    )

    result = service.check_connectivity("example.test", [443, 22], 1.0)

    assert result.status == "partially_reachable"
    assert [item.port for item in result.results] == [443, 22]
    assert [item.status for item in result.results] == ["reachable", "connection_refused"]


@pytest.mark.parametrize(
    "payload",
    [
        {"target": " ", "ports": [443]},
        {"target": "bad host", "ports": [443]},
        {"target": "example.test", "ports": []},
        {"target": "example.test", "ports": [0]},
        {"target": "example.test", "ports": [65536]},
        {"target": "example.test", "ports": [443, 443]},
        {"target": "example.test", "ports": [443], "timeout_seconds": 0},
        {"target": "example.test", "ports": list(range(1, 18))},
        {"target": "example.test", "ports": [443], "timeout_seconds": 10.1},
    ],
)
def test_request_validation_rejects_unsafe_inputs(payload):
    with pytest.raises(ValidationError):
        ConnectivityCheckRequest(**payload)


def test_request_validation_accepts_maximum_timeout():
    assert ConnectivityCheckRequest(target="example.test", ports=[443], timeout_seconds=10.0)


class FakeDb:
    def __init__(self):
        self.entries = []
        self.commits = 0

    def add(self, entry):
        self.entries.append(entry)

    def commit(self):
        self.commits += 1


def _api_client(fake_db: FakeDb) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    def override_db():
        yield fake_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[routes.get_current_user] = lambda: User("engineer-1", "Engineer")
    return TestClient(app)


def test_api_returns_check_result_and_writes_audit_log(monkeypatch):
    fake_db = FakeDb()
    expected = ConnectivityCheckResponse(
        target="192.0.2.10",
        resolved_addresses=["192.0.2.10"],
        status="reachable",
        reachable=True,
        summary="All requested TCP ports are reachable.",
        results=[
            ConnectivityPortResult(
                port=443,
                status="reachable",
                message="TCP connection succeeded.",
                latency_ms=1.25,
            )
        ],
    )
    monkeypatch.setattr(routes, "check_connectivity", lambda *args: expected)
    monkeypatch.setattr(routes, "is_locked", lambda user_id: False)

    response = _api_client(fake_db).post(
        "/api/connectivity-check",
        json={"target": "192.0.2.10", "ports": [443], "timeout_seconds": 1},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "reachable"
    assert fake_db.commits == 2
    assert fake_db.entries[0].actor == "engineer-1"
    assert fake_db.entries[0].action == "connectivity_check"
    assert json.loads(fake_db.entries[0].detail) == {
        "target": "192.0.2.10",
        "ports": [443],
        "event": "started",
    }
    assert json.loads(fake_db.entries[1].detail)["result"] == "reachable"


def test_api_audits_failed_check_with_escaped_target(monkeypatch):
    fake_db = FakeDb()
    monkeypatch.setattr(routes, "is_locked", lambda user_id: False)
    monkeypatch.setattr(
        routes,
        "check_connectivity",
        lambda *args: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        _api_client(fake_db).post(
            "/api/connectivity-check",
            json={"target": 'example.test"x', "ports": [443]},
        )

    assert fake_db.commits == 2
    assert json.loads(fake_db.entries[0].detail)["target"] == 'example.test"x'
    assert json.loads(fake_db.entries[1].detail)["event"] == "failed"


def test_api_rejects_check_when_session_is_locked(monkeypatch):
    fake_db = FakeDb()
    monkeypatch.setattr(routes, "is_locked", lambda user_id: True)

    response = _api_client(fake_db).post(
        "/api/connectivity-check",
        json={"target": "192.0.2.10", "ports": [443]},
    )

    assert response.status_code == 423
    assert fake_db.entries == []
