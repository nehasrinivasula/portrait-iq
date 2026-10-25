"""Bounded TCP connectivity checks for the pre-diagnostics workflow.

The check deliberately reports observed socket outcomes. A TCP timeout can be
caused by a firewall, packet loss, or an unresponsive target, so it is reported
as "timeout" rather than claiming that a remote port is definitely blocked.
"""
from __future__ import annotations

import errno
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass

from app.schemas import ConnectivityCheckResponse, ConnectivityPortResult

MAX_WORKERS = 8
DNS_WORKERS = 4

# These executors are shared by every request. This keeps simultaneous checks
# from multiplying the number of threads and sockets used by the process.
_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=DNS_WORKERS, thread_name_prefix="connectivity-dns")
_CONNECT_EXECUTOR = ThreadPoolExecutor(
    max_workers=MAX_WORKERS,
    thread_name_prefix="connectivity-tcp",
)
_SOCKET_SLOTS = threading.BoundedSemaphore(MAX_WORKERS)


@dataclass(frozen=True)
class ResolvedEndpoint:
    family: int
    socket_type: int
    protocol: int
    address: tuple

    def for_port(self, port: int) -> tuple:
        if self.family == socket.AF_INET6:
            host, _, flow_info, scope_id = self.address
            return host, port, flow_info, scope_id
        host, _ = self.address
        return host, port


def _resolve(target: str) -> list[ResolvedEndpoint]:
    addresses = socket.getaddrinfo(
        target,
        0,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    endpoints: list[ResolvedEndpoint] = []
    seen: set[tuple[int, tuple]] = set()
    for family, socket_type, protocol, _, address in addresses:
        key = (family, address)
        if key in seen:
            continue
        seen.add(key)
        endpoints.append(ResolvedEndpoint(family, socket_type, protocol, address))
    return endpoints


def _resolve_with_timeout(target: str, timeout_seconds: float) -> list[ResolvedEndpoint]:
    """Resolve a target without allowing DNS to hold the request indefinitely."""
    future = _DNS_EXECUTOR.submit(_resolve, target)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        future.cancel()
        raise socket.gaierror(socket.EAI_AGAIN, "DNS resolution timed out") from None


def _display_addresses(endpoints: list[ResolvedEndpoint]) -> list[str]:
    return list(dict.fromkeys(str(endpoint.address[0]) for endpoint in endpoints))


def _status_for_errno(error_number: int) -> tuple[str, str]:
    if error_number == errno.ECONNREFUSED:
        return "connection_refused", "The connection was refused; no TCP service accepted it."
    if error_number == errno.ETIMEDOUT:
        return (
            "timeout",
            "Connection timed out; the target may be unresponsive or traffic may be filtered.",
        )
    if error_number in {errno.EHOSTUNREACH, errno.EHOSTDOWN}:
        return "host_unreachable", "The operating system reported that the host is unreachable."
    if error_number == errno.ENETUNREACH:
        return "network_unreachable", "The operating system reported no route to the network."
    if error_number in {errno.EACCES, errno.EPERM}:
        return (
            "blocked_by_local_policy",
            "The connection was blocked by a local permission or network policy.",
        )
    description = errno.errorcode.get(error_number, f"errno {error_number}")
    return "connection_error", f"Connection failed with {description}."


def _connect(endpoint: ResolvedEndpoint, port: int, timeout_seconds: float) -> int:
    """Connect through a small seam that tests can replace without patching socket globally."""
    with _SOCKET_SLOTS:
        with socket.socket(endpoint.family, endpoint.socket_type, endpoint.protocol) as client:
            client.settimeout(timeout_seconds)
            return client.connect_ex(endpoint.for_port(port))


def _check_port(
    port: int,
    endpoints: list[ResolvedEndpoint],
    timeout_seconds: float,
) -> ConnectivityPortResult:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    failures: list[tuple[str, str]] = []

    for index, endpoint in enumerate(endpoints):
        remaining_seconds = deadline - time.perf_counter()
        if remaining_seconds <= 0:
            failures.append(_status_for_errno(errno.ETIMEDOUT))
            break
        # Do not let an unresponsive address (commonly an IPv6 address) consume
        # the budget before another resolved address gets an attempt.
        endpoint_timeout = remaining_seconds / (len(endpoints) - index)
        attempt_started = time.perf_counter()
        try:
            error_number = _connect(endpoint, port, endpoint_timeout)
        except socket.timeout:
            error_number = errno.ETIMEDOUT
        except OSError as exc:
            error_number = exc.errno or errno.EIO

        if error_number == 0:
            return ConnectivityPortResult(
                port=port,
                status="reachable",
                message="TCP connection succeeded.",
                latency_ms=round((time.perf_counter() - attempt_started) * 1000, 2),
            )
        failures.append(_status_for_errno(error_number))

    # Prefer evidence that the host responded over less specific failures from
    # another resolved address (for example, unreachable IPv6 plus refused IPv4).
    priority = {
        "connection_refused": 0,
        "blocked_by_local_policy": 1,
        "timeout": 2,
        "host_unreachable": 3,
        "network_unreachable": 4,
        "connection_error": 5,
    }
    status, message = min(failures, key=lambda failure: priority[failure[0]])
    return ConnectivityPortResult(
        port=port,
        status=status,
        message=message,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )


def check_connectivity(
    target: str,
    ports: list[int],
    timeout_seconds: float,
) -> ConnectivityCheckResponse:
    """Resolve target and check requested TCP ports with bounded concurrency."""
    try:
        endpoints = _resolve_with_timeout(target, timeout_seconds)
    except (socket.gaierror, UnicodeError, ValueError) as exc:
        message = f"Target name could not be resolved: {exc}."
        return ConnectivityCheckResponse(
            target=target,
            resolved_addresses=[],
            status="unreachable",
            reachable=False,
            summary="Target name could not be resolved.",
            results=[
                ConnectivityPortResult(port=port, status="dns_failure", message=message)
                for port in ports
            ],
        )

    if not endpoints:
        return ConnectivityCheckResponse(
            target=target,
            resolved_addresses=[],
            status="unreachable",
            reachable=False,
            summary="Target did not resolve to a usable TCP address.",
            results=[
                ConnectivityPortResult(
                    port=port,
                    status="dns_failure",
                    message="Target did not resolve to a usable TCP address.",
                )
                for port in ports
            ],
        )

    futures = [
        _CONNECT_EXECUTOR.submit(_check_port, port, endpoints, timeout_seconds)
        for port in ports
    ]
    results = [future.result() for future in futures]

    reachable_count = sum(result.status == "reachable" for result in results)
    if reachable_count == len(results):
        status = "reachable"
        summary = "All requested TCP ports are reachable."
    elif reachable_count:
        status = "partially_reachable"
        summary = f"{reachable_count} of {len(results)} requested TCP ports are reachable."
    else:
        status = "unreachable"
        summary = "None of the requested TCP ports are reachable."

    return ConnectivityCheckResponse(
        target=target,
        resolved_addresses=_display_addresses(endpoints),
        status=status,
        reachable=reachable_count > 0,
        summary=summary,
        results=results,
    )
