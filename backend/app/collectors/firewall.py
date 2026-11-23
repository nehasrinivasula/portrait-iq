"""Read the target node's external-zone firewalld rules over SSH (SCRUM-126)."""

import ipaddress
import subprocess

from app.collectors.base import Collector, CollectorResult, PluginError
from app.config import settings


class FirewallCollector(Collector):
    name = "firewall"
    display_name = "Firewall settings"
    description = "Captures the target node's external-zone firewalld rules as a text file."

    def run(self, target_ip: str) -> CollectorResult:
        try:
            ipaddress.IPv4Address(target_ip)
        except ValueError:
            raise PluginError("Firewall settings require a valid target IP address.") from None

        out_dir = self.require_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / "firewall-info.txt"
        artifact.unlink(missing_ok=True)  # A retry must never export an older capture.
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "ConnectTimeout=10"]
        if settings.firewall_ssh_identity_file:
            argv += ["-i", settings.firewall_ssh_identity_file]
        argv += [f"gehc@{target_ip}", "sudo", "-n", "firewall-cmd", "--zone=external", "--list-all"]

        try:
            result = subprocess.run(argv, capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PluginError(
                f"Firewall SSH command failed ({type(exc).__name__}).",
                safe_reason="Firewall settings could not be collected over SSH.",
            ) from None
        if result.returncode != 0:
            # SSH and sudo output may contain host details; never put it in results.json.
            raise PluginError("Firewall settings could not be collected over SSH.")
        if not result.stdout.strip():
            raise PluginError("Firewall command returned no settings.")
        if len(result.stdout) > self.limits.max_output_mb * 1024 * 1024:
            raise PluginError("Firewall settings exceeded the configured output limit.")
        try:
            text = result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            raise PluginError("Firewall command returned non-text output.") from None
        artifact.write_text(text, encoding="utf-8")
        return CollectorResult(
            tool=self.name,
            artifact_paths=[str(artifact)],
            summary="External-zone firewall settings captured.",
        )

    def cleanup(self) -> None:
        """The read-only SSH command leaves no managed resources behind."""
