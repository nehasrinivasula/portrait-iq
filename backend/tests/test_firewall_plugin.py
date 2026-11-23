"""SCRUM-126: SSH capture stays read-only and enters the normal artifact path."""

import subprocess

import pytest

from app.collectors.base import PluginError, ResourceLimits
from app.collectors.firewall import FirewallCollector
from app.config import settings


def collector(tmp_path):
    plugin = FirewallCollector(ResourceLimits(cpu_pct=50, mem_mb=512, max_output_mb=1))
    plugin.configure({})
    plugin.bind_job("job-1", tmp_path / "firewall")
    return plugin


def test_capture_external_zone_as_text(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "firewall_ssh_identity_file", "/mounted/ssh/id_ed25519")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"external (active)\n  services: ssh\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = collector(tmp_path).run("192.0.2.10")
    argv, kwargs = calls[0]
    assert argv[-6:] == ["gehc@192.0.2.10", "sudo", "-n", "firewall-cmd", "--zone=external", "--list-all"]
    assert argv[argv.index("-i") + 1] == "/mounted/ssh/id_ed25519"
    assert "BatchMode=yes" in argv and "StrictHostKeyChecking=yes" in argv
    assert kwargs["timeout"] == 30
    assert result.artifact_paths == [str(tmp_path / "firewall" / "firewall-info.txt")]
    assert (tmp_path / "firewall" / "firewall-info.txt").read_text() == "external (active)\n  services: ssh\n"


@pytest.mark.parametrize("response", [
    subprocess.CompletedProcess([], 255, b"", b"private host detail"),
    subprocess.CompletedProcess([], 0, b"", b""),
    subprocess.CompletedProcess([], 0, b"x" * (1024 * 1024 + 1), b""),
])
def test_failure_does_not_leave_stale_artifact(tmp_path, monkeypatch, response):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: response)
    plugin = collector(tmp_path)
    plugin.require_output_dir().mkdir()
    artifact = plugin.require_output_dir() / "firewall-info.txt"
    artifact.write_text("old capture")
    with pytest.raises(PluginError) as error:
        plugin.run("192.0.2.10")
    assert "private host detail" not in str(error.value)
    assert not artifact.exists()


def test_rejects_non_ip_without_running_ssh(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("SSH ran"))
    with pytest.raises(PluginError, match="valid target IP"):
        collector(tmp_path).run("host; echo unsafe")
