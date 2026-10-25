"""
UMF RTI: which DDS participants, topics and types are live on a UMF domain.

Runs GE's axone-umf-rtitools image (Connext Pro plus the domain's security
files; never built here, it comes from GE) as a short-lived host-network pod,
and runs rtiddsspy in it for duration_s with the target as discovery peer.

Discovery only, never message contents: rtiddsspy prints sample contents
only with -printSample, which is never passed, and the parser keeps only the
writer/reader/data lines (topic and type names, never values). UMF samples
can carry patient data.

Per Andrew Welch's US-side note the traffic is encrypted, so what shows up
depends on the certificates the image carries: seeing nothing becomes a
warning, not an empty success.

The rtiddsspy line format is taken from RTI's documentation and has not been
checked against the bench yet; unknown lines are kept in the raw output and
ignored by the summary.
"""
import json
import re
import shlex

from app.collectors import k8s
from app.collectors.base import Collector, CollectorResult, PluginConfigError, PluginError
from app.collectors.netshoot import is_host
from app.config import settings
from app.secrets_loader import redact

# 7 logging, 12 source pod ("umfgeneral"), 13 view pod (GE's rtitools README).
DOMAINS = (7, 12, 13)

# e.g.  1698077520.123456 New writer   from 10.0.0.1   "Topic"   "Type"
SPY_LINE = re.compile(
    r"\b(?P<event>New writer|New reader|New data|Deleted writer|Deleted reader|"
    r"Updated writer|Updated reader)\s+from\s+(?P<host>\S+)\s+"
    r"\"(?P<topic>[^\"]*)\"\s+\"(?P<type>[^\"]*)\""
)

TIMED_OUT = 124   # coreutils timeout: the expected way rtiddsspy ends


class RtiCollector(Collector):
    name = "rti"
    display_name = "UMF RTI"
    description = "Lists the DDS devices, topics and types live on a UMF domain (no message contents)."

    DEFAULT_DOMAIN = 12
    DEFAULT_DURATION_S = 30
    MAX_DURATION_S = 300

    def __init__(self, limits):
        super().__init__(limits)
        self.domain = self.DEFAULT_DOMAIN
        self.duration_s = self.DEFAULT_DURATION_S
        self._runner = None
        self._pod: str | None = None

    def configure(self, options):
        """Options: domain (7, 12 or 13; default 12) and duration_s
        (10 to 300 seconds of listening; default 30)."""
        unknown = sorted(set(options) - {"domain", "duration_s"})
        if unknown:
            raise PluginConfigError(f"rti does not accept options: {unknown}")
        if not settings.rti_enabled:
            raise PluginConfigError("UMF RTI can't run: GE's rtitools image is missing.")
        domain = options.get("domain", self.DEFAULT_DOMAIN)
        if domain not in DOMAINS or isinstance(domain, bool):
            raise PluginConfigError(f"rti domain must be one of {list(DOMAINS)}")
        duration_s = options.get("duration_s", self.DEFAULT_DURATION_S)
        if (not isinstance(duration_s, int) or isinstance(duration_s, bool)
                or not 10 <= duration_s <= self.MAX_DURATION_S):
            raise PluginConfigError(f"rti duration_s must be a whole number from 10 to {self.MAX_DURATION_S}")
        self.domain, self.duration_s = domain, duration_s
        self.options = {"domain": domain, "duration_s": duration_s}

    def audit_fields(self):
        return dict(self.options)

    def spy_argv(self, target_ip: str) -> list[str]:
        argv = ["timeout", str(self.duration_s), "rtiddsspy", "-domainId", str(self.domain),
                "-peer", target_ip]
        if settings.rti_qos_file:
            argv += ["-qosFile", settings.rti_qos_file]
        if settings.rti_qos_profile:
            argv += ["-qosProfile", settings.rti_qos_profile]
        return argv

    def run(self, target_ip: str) -> CollectorResult:
        if not is_host(target_ip):
            raise PluginError(f"rti: target {target_ip!r} is not an IP address or host name")
        out_dir = self.require_output_dir()
        argv = self.spy_argv(target_ip)
        if self._pod:            # run() again after a partial failure: start clean
            self.cleanup()

        start_s = settings.tool_pod_start_timeout_seconds
        deadline = start_s + self.duration_s + 60
        self._runner = k8s.get_pod_runner()
        self._pod = k8s.pod_name(self.name, self.job_id)   # set first, so cleanup() finds it
        # Host network: DDS discovery uses the node's interfaces. No extra
        # capabilities: a participant only needs ordinary UDP sockets.
        self._runner.create(
            name=self._pod, tool=self.name, job_id=self.job_id,
            image=settings.rti_image, command=["sleep", str(deadline)],
            deadline_s=deadline, limits=self.limits, host_network=True,
        )
        self._runner.wait_running(self._pod, start_s)
        res = self._runner.exec(self._pod, argv, self.duration_s + 30)
        stdout, stderr = redact(res.stdout), redact(res.stderr)
        if res.exit_code not in (0, TIMED_OUT):
            lines = (stderr or stdout).strip().splitlines()
            reason = lines[-1][:200] if lines else f"exit code {res.exit_code}"
            raise PluginError(f"rtiddsspy failed ({reason})")

        found = summarize(stdout)
        (out_dir / "rti.json").write_text(json.dumps(
            {"domain": self.domain, "duration_s": self.duration_s, "command": shlex.join(argv), **found},
            indent=2,
        ))
        (out_dir / "rti.txt").write_text(stdout + (f"\n[stderr]\n{stderr}" if stderr.strip() else ""))

        warnings = []
        if not found["topics"]:
            warnings.append(
                f"No DDS participants seen on domain {self.domain} in {self.duration_s}s. "
                "The target may be off or unreachable, or the image's certificates may not "
                "match this site's DDS security."
            )
        if res.truncated:
            warnings.append("rtiddsspy output was cut at the size limit; the summary may be incomplete")
        return CollectorResult(
            tool=self.name,
            artifact_paths=[str(out_dir / "rti.json"), str(out_dir / "rti.txt")],
            summary=(f"{len(found['hosts'])} hosts, {len(found['topics'])} topics on domain "
                     f"{self.domain} over {self.duration_s}s"),
            warnings=warnings,
        )

    def cleanup(self) -> None:
        if self._pod and self._runner:
            self._runner.delete(self._pod)
        self._pod = None


def summarize(spy_output: str) -> dict:
    """Hosts and topics from rtiddsspy's discovery lines: per topic its type,
    writer/reader hosts and how many samples arrived (never their contents)."""
    hosts, topics = set(), {}
    for m in SPY_LINE.finditer(spy_output):
        hosts.add(m["host"])
        t = topics.setdefault(m["topic"], {"type": m["type"], "writers": set(), "readers": set(), "samples": 0})
        if m["event"].endswith("writer"):
            t["writers"].add(m["host"])
        elif m["event"].endswith("reader"):
            t["readers"].add(m["host"])
        else:
            t["samples"] += 1
    return {
        "hosts": sorted(hosts),
        "topics": {
            name: {"type": t["type"], "writers": sorted(t["writers"]),
                   "readers": sorted(t["readers"]), "samples": t["samples"]}
            for name, t in sorted(topics.items())
        },
    }
