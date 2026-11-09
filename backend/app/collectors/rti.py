"""
UMF RTI: which DDS participants, topics and types are live on a UMF domain.

Runs GE's axone-umf-rtitools image (Connext Pro plus the domain's security
files; never built here, it comes from GE) as a short-lived host-network pod,
and runs rtiddsspy in it for duration_s.

The Admin Console reaches each UMF domain over RTI Real-Time WAN through the
router's WAN NodePort, not plain UDP discovery -- proven on the bench
2026-11-08 (plain UDP with the data-node profile never completes; the router
only admits participants over its WAN endpoints). So the spy runs with the
-WithWAN QoS and the domain's umfwan_server_* profile, with DDS_SECURE=secure
and the DDS_RTWAN_SERVER_* env the profile reads, and peers at
udpv4_wan://<router>:<NodePort> (32007 logger/7, 32013 source/12, 32015 view/13).

Discovery only, never message contents: rtiddsspy prints sample contents only
with -printSample, which is never passed, and the parser keeps only the
writer/reader discovery lines (topic and type names, never values). UMF
samples can carry patient data.

The traffic is encrypted, so what shows up depends on the image's identity
being permitted to subscribe (the SvcAxRtiTools cert is): seeing nothing
becomes a warning, not an empty success. The rtiddsspy table format the parser
reads was confirmed on the bench 2026-11-08; a long type name can be truncated
to its column width, and the full output is kept in rti.txt.
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

# RTI Real-Time WAN wiring, proven on the bench 2026-11-08. Per domain: the
# name its QoS profile and DDS_RTWAN_SERVER_PORT_* env use, and the router
# NodePort (svc axone-router-1-np) to peer at over UDPv4_WAN.
WAN_BY_DOMAIN = {
    7:  ("logger", 32007),
    12: ("sourcepod", 32013),
    13: ("viewpod", 32015),
}


def rtps_port_range(domain: int) -> tuple[int, int]:
    """The UDP ports RTPS uses on a domain: 250 from 7400 + 250 * domain."""
    first = 7400 + 250 * domain
    return first, first + 249

# rtiddsspy's discovery table (confirmed on the bench 2026-11-08), e.g.:
#   1794181298.729353  R +N  C0A80003  umflog  umflog::S35c_sapphire
# Info starts with W (writer) or R (reader) then a change code (+N, -D, ...);
# Src HostId is hex. Data-sample lines are a different shape and, so sample
# contents never surface, are deliberately left unmatched.
RTI_SPY_LINE = re.compile(
    r"^\s*\d+\.\d+\s+"
    r"(?P<kind>[WR])\s*[-+]\S*\s+"
    r"(?P<host>[0-9A-Fa-f]+)\s+"
    r"(?P<topic>\S+)\s+"
    r"(?P<type>\S+)\s*$",
    re.MULTILINE,
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

    def spy_argv(self, node_ip: str, target_ip: str) -> list[str]:
        """The rtiddsspy command, wired for RTI Real-Time WAN. node_ip is the
        tool pod's node (from wait_running): the WAN public address a
        host-network pod advertises, and the default router host."""
        role, nodeport = WAN_BY_DOMAIN[self.domain]
        public_address = settings.rti_wan_public_address or node_ip or target_ip
        router_host = settings.rti_wan_router_host or target_ip or node_ip
        local_port = nodeport + settings.rti_wan_local_port_offset
        peer = settings.rti_peer or f"udpv4_wan://{router_host}:{nodeport}"
        profile = settings.rti_qos_profile or f"AxoneNetQoSLibrary::umfwan_server_{role}_qos"
        # DDS_SECURE=secure selects the secured QoS chain (the image env ships
        # 'unsecure'); the DDS_RTWAN_SERVER_* vars fill the $()s the WAN profile
        # reads. `env` adds them to the exec without a shell.
        argv = [
            "env",
            "DDS_SECURE=secure",
            f"DDS_RTWAN_SERVER_PUBLIC_ADDRESS={public_address}",
            f"DDS_RTWAN_SERVER_PORT_{role.upper()}={local_port}",
            "timeout", str(self.duration_s), "rtiddsspy",
            "-domainId", str(self.domain), "-peer", peer,
        ]
        if settings.rti_qos_file:
            argv += ["-qosFile", settings.rti_qos_file]
        if profile:
            argv += ["-qosProfile", profile]
        return argv

    def run(self, target_ip: str) -> CollectorResult:
        if not is_host(target_ip):
            raise PluginError(f"rti: target {target_ip!r} is not an IP address or host name")
        out_dir = self.require_output_dir()
        if self._pod:            # run() again after a partial failure: start clean
            self.cleanup()

        start_s = settings.tool_pod_start_timeout_seconds
        deadline = start_s + self.duration_s + 60
        self._runner = k8s.get_pod_runner()
        self._pod = k8s.pod_name(self.name, self.job_id)   # set first, so cleanup() finds it
        # Host network: the WAN transport advertises the node's IP and binds
        # its port there, so the router can reach this participant. No extra
        # capabilities: a participant only needs ordinary UDP sockets.
        # The image's own DDS security files are the SvcAxRtiTools identity,
        # which the domain permits to subscribe (confirmed on the bench), so no
        # Secret is needed by default. One can still be mounted to override them.
        secret_mount = (
            (settings.rti_security_secret, settings.rti_sdds_dir)
            if settings.rti_security_secret else None
        )
        self._runner.create(
            name=self._pod, tool=self.name, job_id=self.job_id,
            image=settings.rti_image, command=["sleep", str(deadline)],
            deadline_s=deadline, limits=self.limits, host_network=True,
            secret_mount=secret_mount,
        )
        node_ip = self._runner.wait_running(self._pod, start_s)
        # Built now: the WAN public address is the node this pod landed on.
        argv = self.spy_argv(node_ip or "", target_ip)
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
                "Nothing may be publishing on that domain, or the router's WAN endpoint "
                "may be unreachable from here."
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
    """Hosts and topics from rtiddsspy's discovery lines: per topic its type
    and the writer/reader host ids (never sample contents, which the parser
    does not match)."""
    hosts, topics = set(), {}
    for m in RTI_SPY_LINE.finditer(spy_output):
        hosts.add(m["host"])
        t = topics.setdefault(m["topic"], {"type": m["type"], "writers": set(), "readers": set()})
        (t["writers"] if m["kind"] == "W" else t["readers"]).add(m["host"])
    return {
        "hosts": sorted(hosts),
        "topics": {
            name: {"type": t["type"], "writers": sorted(t["writers"]), "readers": sorted(t["readers"])}
            for name, t in sorted(topics.items())
        },
    }
