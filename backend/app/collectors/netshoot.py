"""
Netshoot: network checks from inside the cluster (DNS, ports, routes).
Uses the nicolaka/netshoot image, the requirements doc's approved exception
to the Ubuntu 22 base image (D-11a).

run() starts one short-lived pod in our namespace (collectors/k8s.py), runs
each check in it with exec, saves the output and returns. cleanup() deletes
the pod. Checks are argument lists, never shell strings, and every host or
port in them was validated in configure(). The pod gets NET_RAW only (ping,
traceroute) and no Kubernetes API access.

Deliberately not `kubectl debug` on Portrait's own pods: an ephemeral
container cannot be removed again without restarting that pod, which breaks
"the customer environment returns to its original state" (C-4).

A failing check is a finding, not a plugin failure: it becomes a warning.
The plugin itself fails only when the pod cannot run.
"""
import ipaddress
import json
import re
from dataclasses import dataclass

from app.collectors import k8s
from app.collectors.base import Collector, CollectorResult, PluginConfigError, PluginError
from app.config import settings
from app.secrets_loader import redact

HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$"
)


def is_host(value) -> bool:
    """An IP address or a DNS name. Never starts with '-', so it cannot be
    read as a command-line option."""
    if not isinstance(value, str):
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(HOST_PATTERN.match(value))


@dataclass
class Check:
    label: str
    argv: list[str]
    needs_output: bool = False   # dig +short exits 0 even when a name does not resolve


class NetshootCollector(Collector):
    name = "netshoot"
    display_name = "Netshoot"
    description = "Network tests from inside the cluster: DNS, ports, routes."

    # Open question for the sponsor: which ports matter on a Portrait site.
    # Until then: SSH, HTTPS and the Kubernetes API on the MiniEdge.
    DEFAULT_PORTS = [22, 443, 6443]
    DEFAULT_DNS_NAMES = [
        "kubernetes.default.svc.cluster.local",
        "axone-log-export-tcp.pm.svc.cluster.local",
    ]
    MAX_ITEMS = 20
    CHECK_TIMEOUT_S = 30

    def __init__(self, limits):
        super().__init__(limits)
        self.ports = list(self.DEFAULT_PORTS)
        self.dns_names = list(self.DEFAULT_DNS_NAMES)
        self.extra_targets: list[str] = []
        self._runner = None
        self._pod: str | None = None

    def configure(self, options):
        """Options: ports (list of TCP ports), dns_names (names to resolve)
        and extra_targets (hosts to test besides the job's target), each at
        most MAX_ITEMS long. All optional."""
        unknown = sorted(set(options) - {"ports", "dns_names", "extra_targets"})
        if unknown:
            raise PluginConfigError(f"netshoot does not accept options: {unknown}")
        ports = self._list(options, "ports", self.DEFAULT_PORTS)
        if not all(isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 65535 for p in ports):
            raise PluginConfigError("netshoot ports must be whole numbers from 1 to 65535")
        dns_names = self._list(options, "dns_names", self.DEFAULT_DNS_NAMES)
        extra_targets = self._list(options, "extra_targets", [])
        for value in (*dns_names, *extra_targets):
            if not is_host(value):
                raise PluginConfigError(f"netshoot: {value!r} is not an IP address or host name")
        self.ports, self.dns_names, self.extra_targets = ports, dns_names, extra_targets
        self.options = {"ports": ports, "dns_names": dns_names, "extra_targets": extra_targets}

    def _list(self, options, key, default):
        value = options.get(key, default)
        if not isinstance(value, list):
            raise PluginConfigError(f"netshoot {key} must be a list")
        if len(value) > self.MAX_ITEMS:
            raise PluginConfigError(f"netshoot {key} is limited to {self.MAX_ITEMS} entries")
        return list(value)

    def checks(self, target_ip: str) -> list[Check]:
        checks = [
            Check("pod addresses", ["ip", "-brief", "address"]),
            Check("routes", ["ip", "route"]),
            Check("DNS settings", ["cat", "/etc/resolv.conf"]),
        ]
        for name in self.dns_names:
            checks.append(Check(f"resolve {name}", ["dig", "+short", "+time=3", "+tries=1", name],
                                needs_output=True))
        for host in [target_ip, *self.extra_targets]:
            checks.append(Check(f"ping {host}", ["ping", "-c", "3", "-W", "2", host]))
            checks.append(Check(f"route to {host}",
                                ["traceroute", "-n", "-w", "2", "-q", "1", "-m", "10", host]))
            for port in self.ports:
                checks.append(Check(f"TCP {host}:{port}", ["nc", "-z", "-v", "-w", "3", host, str(port)]))
        return checks

    def run(self, target_ip: str) -> CollectorResult:
        if not is_host(target_ip):
            raise PluginError(f"netshoot: target {target_ip!r} is not an IP address or host name")
        out_dir = self.require_output_dir()
        checks = self.checks(target_ip)
        if self._pod:            # run() again after a partial failure: start clean
            self.cleanup()

        start_s = settings.tool_pod_start_timeout_seconds
        deadline = min(start_s + len(checks) * self.CHECK_TIMEOUT_S + 60, 3600)
        self._runner = k8s.get_pod_runner()
        self._pod = k8s.pod_name(self.name, self.job_id)   # set first, so cleanup() finds it
        self._runner.create(
            name=self._pod, tool=self.name, job_id=self.job_id,
            image=settings.netshoot_image, command=["sleep", str(deadline)],
            deadline_s=deadline, limits=self.limits, add_capabilities=("NET_RAW",),
        )
        self._runner.wait_running(self._pod, start_s)

        records, warnings = [], []
        for check in checks:
            res = self._runner.exec(self._pod, check.argv, self.CHECK_TIMEOUT_S)
            stdout, stderr = redact(res.stdout), redact(res.stderr)
            ok = res.exit_code == 0 and (bool(stdout.strip()) or not check.needs_output)
            records.append({
                "check": check.label, "command": " ".join(check.argv), "ok": ok,
                "exit_code": res.exit_code, "stdout": stdout, "stderr": stderr,
                "truncated": res.truncated,
            })
            if not ok:
                warnings.append(f"{check.label} failed: {_why(res.exit_code, stdout, stderr)}")

        (out_dir / "netshoot.json").write_text(json.dumps(records, indent=2))
        (out_dir / "netshoot.txt").write_text(_report(target_ip, records))
        passed = sum(r["ok"] for r in records)
        return CollectorResult(
            tool=self.name,
            artifact_paths=[str(out_dir / "netshoot.txt"), str(out_dir / "netshoot.json")],
            summary=f"{passed} of {len(records)} network checks passed against {target_ip}",
            warnings=warnings,
        )

    def cleanup(self) -> None:
        if self._pod and self._runner:
            self._runner.delete(self._pod)
        self._pod = None


def _why(exit_code, stdout, stderr) -> str:
    if exit_code is None:
        return "timed out"
    if exit_code == 0:
        return "no answer"     # dig +short with an empty result
    lines = (stderr or stdout).strip().splitlines()
    return (lines[-1][:200] if lines else f"exit code {exit_code}") or f"exit code {exit_code}"


def _report(target_ip, records) -> str:
    parts = [f"Netshoot checks against {target_ip}\n"]
    for r in records:
        parts.append(f"== {'PASS' if r['ok'] else 'FAIL'}  {r['check']}\n$ {r['command']}\n")
        parts.append((r["stdout"] + r["stderr"]).rstrip() + "\n")
        if r["truncated"]:
            parts.append("[output truncated]\n")
        parts.append("\n")
    return "".join(parts)
