import errno
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


class FakeSocket:
    def __init__(self, error_number: int | dict[int, int]):
        self.error_number = error_number
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def settimeout(self, timeout: float):
        self.timeout = timeout

    def connect_ex(self, address: tuple) -> int:
        if isinstance(self.error_number, dict):
            return self.error_number[address[1]]
        return self.error_number


def _endpoint() -> service.ResolvedEndpoint:
    return service.ResolvedEndpoint(socket.AF_INET, socket.SOCK_STREAM, 6, ("192.0.2.10", 0))


def test_reachable_port(monkeypatch):
    monkeypatch.setattr(service, "_resolve", lambda target: [_endpoint()])
    monkeypatch.setattr(service.socket, "socket", lambda *args: FakeSocket(0))

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
    monkeypatch.setattr(service.socket, "socket", lambda *args: FakeSocket(error_number))

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


def test_partial_reachability_preserves_requested_port_order(monkeypatch):
    monkeypatch.setattr(service, "_resolve", lambda target: [_endpoint()])
    monkeypatch.setattr(
        service.socket,
        "socket",
        lambda *args: FakeSocket({443: 0, 22: errno.ECONNREFUSED}),
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
    ],
)
def test_request_validation_rejects_unsafe_inputs(payload):
    with pytest.raises(ValidationError):
        ConnectivityCheckRequest(**payload)


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
    assert fake_db.commits == 1
    assert fake_db.entries[0].actor == "engineer-1"
    assert fake_db.entries[0].action == "connectivity_check"
    assert fake_db.entries[0].detail == "target=192.0.2.10 ports=[443] result=reachable"


def test_api_rejects_check_when_session_is_locked(monkeypatch):
    fake_db = FakeDb()
    monkeypatch.setattr(routes, "is_locked", lambda user_id: True)

    response = _api_client(fake_db).post(
        "/api/connectivity-check",
        json={"target": "192.0.2.10", "ports": [443]},
    )

    assert response.status_code == 423
    assert fake_db.entries == []
