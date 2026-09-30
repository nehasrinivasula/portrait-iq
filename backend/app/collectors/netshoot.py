"""
Netshoot: network tests (DNS, ports, routes). Uses its own image — the
requirements doc calls this out as an approved exception to the standard
Ubuntu 22 base image.

TODO: deploy as a short-lived pod via the Kubernetes API against the
MiniEdge P360 (namespace `portrait`), exec the actual checks (e.g. nc -zv,
dig, traceroute), capture stdout, then delete the pod in cleanup().
"""
from app.collectors.base import Collector, CollectorResult


class NetshootCollector(Collector):
    name = "netshoot"

    def run(self, target_ip: str) -> CollectorResult:
        raise NotImplementedError(
            "TODO: kubectl-style pod deploy + exec against the MiniEdge. "
            f"Would test connectivity to {target_ip} within limits {self.limits}."
        )

    def cleanup(self) -> None:
        raise NotImplementedError("TODO: delete the netshoot pod, always, even on failure.")
