import socket

from app.connection_setup import inspect_connection, tool_host_interfaces
from app.connectivity import service


def test_connection_setup_does_not_invent_remote_settings(monkeypatch):
    endpoint = service.ResolvedEndpoint(socket.AF_INET, socket.SOCK_STREAM, 6, ("192.0.2.15", 0))
    monkeypatch.setattr(service, "_resolve", lambda target: [endpoint])
    monkeypatch.setattr(service, "_connect", lambda *args: 0)
    monkeypatch.setattr("app.connection_setup.socket.if_nameindex", lambda: [(1, "en0"), (2, "lo0")])

    result = inspect_connection("192.0.2.15", "live")

    assert result.settings.node_ips == ["192.0.2.15"]
    assert result.settings.interfaces == ["en0", "lo0"]
    assert result.settings.dds_domain_ids == []
    assert any("not guessed" in note for note in result.notes)


def test_tool_host_interfaces_handles_unavailable_platform(monkeypatch):
    monkeypatch.setattr("app.connection_setup.socket.if_nameindex", lambda: (_ for _ in ()).throw(OSError("no interfaces")))
    assert tool_host_interfaces() == []
