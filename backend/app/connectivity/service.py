"""Bounded TCP connectivity checks for the pre-diagnostics workflow.

The check deliberately reports observed socket outcomes. A TCP timeout can be
caused by a firewall, packet loss, or an unresponsive target, so it is reported
as "timeout" rather than claiming that a remote port is definitely blocked.
"""
from __future__ import annotations

import errno
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.schemas import ConnectivityCheckResponse, ConnectivityPortResult

MAX_WORKERS = 8


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


def _display_addresses(endpoints: list[ResolvedEndpoint]) -> list[str]:
    return list(dict.fromkeys(str(endpoint.address[0]) for endpoint in endpoints))


def _status_for_errno(error_number: int) -> tuple[str, str]:
    if error_number == errno.ECONNREFUSED:
        return "connection_refused", "Host is reachable, but the port refused the connection."
    if error_number in {errno.ETIMEDOUT, getattr(errno, "EWOULDBLOCK", errno.ETIMEDOUT)}:
        return (
            "timeout",
            "Connection timed out; the target may be unresponsive or traffic may be filtered.",
        )
    if error_number in {errno.EHOSTUNREACH, getattr(errno, "EHOSTDOWN", errno.EHOSTUNREACH)}:
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


def _check_port(
    port: int,
    endpoints: list[ResolvedEndpoint],
    timeout_seconds: float,
) -> ConnectivityPortResult:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    failures: list[tuple[str, str]] = []

    for endpoint in endpoints:
        remaining_seconds = deadline - time.perf_counter()
        if remaining_seconds <= 0:
            failures.append(_status_for_errno(errno.ETIMEDOUT))
            break
        try:
            with socket.socket(endpoint.family, endpoint.socket_type, endpoint.protocol) as client:
                # The request timeout is a budget for the whole port check, not
                # a fresh timeout for every IPv4/IPv6 address the name resolves to.
                client.settimeout(remaining_seconds)
                error_number = client.connect_ex(endpoint.for_port(port))
        except socket.timeout:
            error_number = errno.ETIMEDOUT
        except OSError as exc:
            error_number = exc.errno or errno.EIO

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        if error_number == 0:
            return ConnectivityPortResult(
                port=port,
                status="reachable",
                message="TCP connection succeeded.",
                latency_ms=elapsed_ms,
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
        endpoints = _resolve(target)
    except (socket.gaierror, UnicodeError) as exc:
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

    worker_count = min(MAX_WORKERS, len(ports))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(
            executor.map(
                lambda port: _check_port(port, endpoints, timeout_seconds),
                ports,
            )
        )

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
