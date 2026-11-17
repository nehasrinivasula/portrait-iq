"""Safe, replaceable connection-setup provider (SCRUM-99).

This module deliberately makes only bounded TCP checks.  A single IP address
and request-scoped credentials cannot reliably reveal GE Kubernetes settings
or DDS domains, so this provider never invents them.  A future approved lab
adapter can replace ``inspect_connection`` while retaining the API contract.
"""
from __future__ import annotations

import socket

from app.config import settings
from app.connectivity.service import check_connectivity
from app.schemas import ConnectResponse, ConnectionSettingsOut


CONNECTION_PORTS = (22, 443)


def tool_host_interfaces() -> list[str]:
    """Return interface names on the machine running Portrait IQ.

    Interface enumeration is local-only and intentionally does not probe the
    requested target.  An unavailable platform simply yields no values.
    """
    try:
        return sorted({name for _, name in socket.if_nameindex()})
    except OSError:
        return []


def inspect_connection(ip_address: str, mode: str) -> ConnectResponse:
    """Build safe prefilled settings without storing connection credentials."""
    connectivity = check_connectivity(ip_address, list(CONNECTION_PORTS), 3.0)
    notes = [
        "Connectivity is a bounded TCP preflight on ports 22 and 443; it does not authenticate to the target.",
        "Namespace is the configured Portrait IQ tools namespace.",
        "Interfaces are on the Portrait IQ tool host, not inferred remote interfaces.",
        "DDS domain IDs are not guessed. An approved RTI discovery provider is required to populate them.",
    ]
    return ConnectResponse(
        mode=mode,
        ip_address=ip_address,
        connectivity=connectivity,
        settings=ConnectionSettingsOut(
            namespace=settings.tools_namespace,
            node_ips=connectivity.resolved_addresses,
            interfaces=tool_host_interfaces(),
            dds_domain_ids=[],
        ),
        notes=notes,
    )
