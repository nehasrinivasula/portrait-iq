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

Import mode (SCRUM-123): the live spy sees the DDS *system* (participants,
topics, types), but RTI's Monitor performance data and the Admin Console's own
saved files exist only as GUI exports -- there is no file to pull off the node
and no headless way to produce them. So the GUI route instead has the operator
save those files from the RTI tools and upload them, and this collector ingests
an upload in place of running the spy: it stores the raw file as a job artifact
and, for the Admin Console XML export, summarizes its topology. The Monitor
".ser" is a Java-serialized session that only RTI Monitor can read, so it is
kept as-is. Import mode needs no image, no pod and no network.
"""
import hashlib
import json
import re
import shlex
import threading
import xml.etree.ElementTree as ET

from app import uploads
from app.collectors import javaser, k8s
from app.collectors.base import Collector, CollectorResult, PluginConfigError, PluginError
from app.collectors.netshoot import is_host
from app.config import settings
from app.secrets_loader import redact

# Import mode: the RTI GUI exports we ingest, as artifact key ->
# (upload kind, stored file extension, label). The upload kind's accepted
# first bytes are enforced in uploads.py.
ARTIFACTS = {
    "admin_console": ("rti_admin", "xml", "RTI Admin Console discovery export"),
    "monitor": ("rti_monitor", "ser", "RTI Monitor saved session"),
}

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
    r"(?P<kind>[WR])\s*(?P<change>[-+])\S*\s+"
    r"(?P<host>[0-9A-Fa-f]+)\s+"
    r"(?P<topic>\S+)\s+"
    r"(?P<type>\S+)\s*$",
    re.MULTILINE,
)

TIMED_OUT = 124   # coreutils timeout: the expected way rtiddsspy ends

# A host-network RTI run binds a fixed local WAN port per domain
# (NodePort + rti_wan_local_port_offset), so two runs on the *same* domain on the
# same node fight for that port and one can silently fail to form its DDS
# participant (SCRUM-39 review). The backend is single-replica, so a process-wide
# guard is enough: one run per domain at a time. Different domains use different
# ports and still run in parallel.
#
# The claim maps the domain to the pod that holds it, not just the domain, so a
# claim can be reconciled against reality: if cleanup() could not confirm the
# pod's deletion it deliberately keeps the claim (the pod may still hold the
# port), but the instance that could retry is then discarded by the
# orchestrator's cleanup loop. So a new run treats a claim whose pod no longer
# exists as stale and takes the domain over -- otherwise a single failed delete
# would block the domain until the backend restarted (SCRUM-39 follow-up review).
_active_domains: dict[int, str] = {}
_active_lock = threading.Lock()


class RtiCollector(Collector):
    name = "rti"
    display_name = "UMF RTI"
    description = "Lists the DDS devices, topics and types live on a UMF domain (no message contents)."

    DEFAULT_DOMAIN = 12
    DEFAULT_DURATION_S = 30
    MAX_DURATION_S = 300

    def __init__(self, limits):
        super().__init__(limits)
        self.mode = "run"
        self.domain = self.DEFAULT_DOMAIN
        self.duration_s = self.DEFAULT_DURATION_S
        self.artifact: str | None = None
        self.upload_id: str | None = None
        self._runner = None
        self._pod: str | None = None
        self._claimed_domain: int | None = None

    def configure(self, options):
        """Run mode (default): domain (7, 12 or 13; default 12) and duration_s
        (10 to 300 s; default 30) -- runs the live spy.
        Import mode (mode="import"): artifact ("admin_console" or "monitor")
        and the upload_id of the matching POST /api/uploads file -- ingests a
        saved RTI GUI export instead of running anything."""
        mode = options.get("mode", "run")
        if mode not in ("run", "import"):
            raise PluginConfigError("rti mode must be 'run' or 'import'")
        if mode == "import":
            self._configure_import(options)
        else:
            self._configure_run(options)
        self.mode = mode

    def _configure_run(self, options):
        unknown = sorted(set(options) - {"mode", "domain", "duration_s"})
        if unknown:
            raise PluginConfigError(f"rti run mode does not accept options: {unknown}")
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
        self.artifact = self.upload_id = None
        self.options = {"domain": domain, "duration_s": duration_s}

    def _configure_import(self, options):
        # No image, pod or network: we only read an uploaded file, so this is
        # allowed even when the rtitools image is absent (rti_enabled is False).
        unknown = sorted(set(options) - {"mode", "artifact", "upload_id"})
        if unknown:
            raise PluginConfigError(f"rti import mode does not accept options: {unknown}")
        artifact = options.get("artifact")
        if artifact not in ARTIFACTS:
            raise PluginConfigError(f"rti import needs artifact one of {sorted(ARTIFACTS)}")
        kind, _ext, label = ARTIFACTS[artifact]
        upload_id = options.get("upload_id")
        if not uploads.is_valid_id(upload_id):
            raise PluginConfigError(
                f"rti import mode needs the upload_id returned by POST /api/uploads?kind={kind}"
            )
        if not uploads.exists(upload_id, kind):
            raise PluginConfigError(f"rti: no uploaded {label} with that upload_id")
        self.artifact, self.upload_id = artifact, upload_id
        self.options = {"mode": "import", "artifact": artifact, "upload_id": upload_id}

    def audit_fields(self):
        return dict(self.options)

    def spy_argv(self, node_ip: str, target_ip: str) -> list[str]:
        """The rtiddsspy command, wired for RTI Real-Time WAN. node_ip is the
        tool pod's node (from wait_running): the WAN public address a
        host-network pod advertises, and the default router host."""
        role, nodeport = WAN_BY_DOMAIN[self.domain]
        public_address = settings.rti_wan_public_address or node_ip or target_ip
        router_host = settings.rti_wan_router_host or target_ip or node_ip
        offset = settings.rti_wan_local_port_offset
        peer = settings.rti_peer or f"udpv4_wan://{router_host}:{nodeport}"
        profile = settings.rti_qos_profile or f"AxoneNetQoSLibrary::umfwan_server_{role}_qos"
        # DDS_SECURE=secure selects the secured QoS chain (the image env ships
        # 'unsecure'); the DDS_RTWAN_SERVER_* vars fill the $()s the WAN profile
        # reads. `env` adds them to the exec without a shell.
        #
        # The shared -WithWAN QoS file references the WAN server port of every
        # UMF domain (LOGGER, SOURCEPOD, VIEWPOD), and RTI expands every $() in
        # the file when it parses it -- before any profile is chosen -- so all
        # three must be defined or the whole file fails to parse, even though we
        # drive one domain. (A run with only the selected domain's port set died
        # on the bench with "Undefined environment variable
        # DDS_RTWAN_SERVER_PORT_LOGGER".)
        argv = [
            "env",
            "DDS_SECURE=secure",
            f"DDS_RTWAN_SERVER_PUBLIC_ADDRESS={public_address}",
        ]
        for r, np in WAN_BY_DOMAIN.values():
            argv.append(f"DDS_RTWAN_SERVER_PORT_{r.upper()}={np + offset}")
        argv += [
            "timeout", str(self.duration_s), "rtiddsspy",
            "-domainId", str(self.domain), "-peer", peer,
        ]
        if settings.rti_qos_file:
            argv += ["-qosFile", settings.rti_qos_file]
        if profile:
            argv += ["-qosProfile", profile]
        return argv

    def run(self, target_ip: str) -> CollectorResult:
        out_dir = self.require_output_dir()
        if self.mode == "import":   # ingest an uploaded GUI export; no target, pod or network
            return self._import(out_dir)
        if not is_host(target_ip):
            raise PluginError(f"rti: target {target_ip!r} is not an IP address or host name")
        if self._pod:            # run() again after a partial failure: start clean
            self.cleanup()

        start_s = settings.tool_pod_start_timeout_seconds
        deadline = start_s + self.duration_s + 60
        namespace = getattr(self, "namespace", None)
        self._runner = k8s.get_pod_runner(namespace) if namespace else k8s.get_pod_runner()
        pod = k8s.pod_name(self.name, self.job_id)

        # One run per domain per node: a second run on this domain would bind the
        # same local WAN port and could silently fail (SCRUM-39 review). The
        # claim is honoured only while its pod still exists -- a claim left behind
        # by a cleanup that could not confirm its delete is reclaimed once that
        # pod is actually gone, so a failed delete cannot block the domain forever.
        with _active_lock:
            holder = _active_domains.get(self.domain)
            if holder is not None and holder != pod and self._runner.exists(holder):
                raise PluginError(
                    f"an RTI diagnostic is already running on domain {self.domain}",
                    safe_reason="Another RTI diagnostic is already running on this domain; try again shortly.",
                )
            _active_domains[self.domain] = pod
        self._pod = pod            # set first, so cleanup() finds it
        self._claimed_domain = self.domain
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
            raise PluginError(
                f"rtiddsspy failed ({reason})",
                safe_reason="The RTI diagnostic could not be completed.",
            )

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

    def _import(self, out_dir) -> CollectorResult:
        """Ingest an uploaded RTI GUI export: store the raw file as a job
        artifact and summarize it -- the Admin Console XML's topology, or the
        Monitor .ser's entities and whether it holds any statistics."""
        kind, ext, label = ARTIFACTS[self.artifact]
        src = uploads.path_for(self.upload_id, kind)
        if not src.is_file():
            raise PluginError(f"rti: the uploaded {label} is gone; upload it again")
        size_mb = src.stat().st_size / (1024 * 1024)
        if size_mb > self.limits.max_output_mb:
            raise PluginError(
                f"rti: the file is {size_mb:.0f} MB, over this job's {self.limits.max_output_mb} MB limit"
            )
        data = src.read_bytes()
        raw = out_dir / f"rti_{self.artifact}.{ext}"
        raw.write_bytes(data)   # keep the original, downloadable and packaged with the job

        warnings = []
        if self.artifact == "admin_console":
            found = summarize_admin_console(data)   # may raise PluginError on a bad file
            info = {"source": "admin_console_export", **found}
            domains = ", ".join(str(d) for d in found["domains"]) or "none"
            summary = (f"Admin Console export: {len(found['participants'])} participants "
                       f"on domains {domains}, {len(found['topic_names'])} topics, "
                       f"{found['writers']} writers, {found['readers']} readers")
            if not found["is_discovery_export"]:
                warnings.append(
                    "This XML is not an Admin Console discovery export (File > Export "
                    "Discovery Data); it was stored but may not be the right file."
                )
            if not found["participants"]:
                warnings.append(
                    "The export was stored but no participants were recognized in it; "
                    "only the raw file is available."
                )
        else:   # monitor: RTI Monitor saved session (Java-serialized)
            found = summarize_monitor_session(data)   # may raise PluginError on a bad file
            info = {
                "source": "monitor_session",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                **found,
            }
            e = found["entities"]
            domains = ", ".join(str(d) for d in found["domains"]) or "none"
            stats = "with statistics" if found["has_statistics"] else "no statistics"
            summary = (f"RTI Monitor session: {e.get('DomainParticipantModel', 0)} participants "
                       f"on domains {domains}, {len(found['topic_names'])} topics, "
                       f"{e.get('DataWriterModel', 0)} writers, {e.get('DataReaderModel', 0)} readers; {stats}")
            if not found["is_monitor_session"]:
                warnings.append(
                    "This file is a Java object stream but not an RTI Monitor saved session "
                    "(File > Save); it was stored but may not be the right file."
                )
            elif found["declared_entities"] != sum(e.values()):
                warnings.append(
                    f"The session lists {found['declared_entities']} entities but "
                    f"{sum(e.values())} were read; it may be incomplete."
                )
            if found["is_monitor_session"] and not found["has_statistics"]:
                warnings.append(
                    "The session holds the system's structure but no Monitor statistics: RTI "
                    "marks every entity as having no monitoring data. That is expected while "
                    "the RTI tools cannot reach the monitoring domain."
                )

        (out_dir / "rti.json").write_text(json.dumps(info, indent=2))
        return CollectorResult(
            tool=self.name,
            artifact_paths=[str(raw), str(out_dir / "rti.json")],
            summary=summary,
            warnings=warnings,
        )

    def cleanup(self) -> None:
        # Release the per-domain claim only once the pod is actually gone. A pod
        # that is still running holds this domain's fixed local WAN port, so a new
        # run on the domain would collide with it (SCRUM-39 review). If delete()
        # raises (Kubernetes could not remove the pod) we keep both the pod
        # reference and the claim: the domain stays blocked. The orchestrator then
        # discards this instance, so no later cleanup() retries -- but a new run
        # reconciles the claim against pod existence and reclaims the domain once
        # that pod has actually gone, so the block cannot outlive the pod. A
        # delete() that returns — including for an already-absent pod, which the
        # runner treats as success — means the pod is gone.
        pod = self._pod
        if pod and self._runner:
            self._runner.delete(pod)   # raises if the pod could not be deleted
        self._pod = None
        if self._claimed_domain is not None:
            with _active_lock:
                # Only drop the claim if we still own it: a new run may have
                # reclaimed this domain as stale and must keep its own claim.
                if _active_domains.get(self._claimed_domain) == pod:
                    del _active_domains[self._claimed_domain]
            self._claimed_domain = None


# Refused anywhere in an uploaded export: a DTD or entity declaration could
# drive entity expansion or an external fetch in the XML parser.
_XML_DTD = re.compile(rb"<!\s*(doctype|entity)", re.IGNORECASE)


def _text(el, path: str) -> str | None:
    """Stripped text at path under el, or None when missing or empty."""
    v = el.findtext(path) if el is not None else None
    v = v.strip() if v else ""
    return v or None


def _int(el, path: str) -> int | None:
    v = _text(el, path)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _version(el) -> str | None:
    """RTI product_version struct (major/minor/release/revision) as "6.1.2.31"."""
    if el is None:
        return None
    parts = [_text(el, k) for k in ("major", "minor", "release", "revision")]
    return ".".join(parts) if all(parts) else None


def summarize_admin_console(xml_bytes: bytes) -> dict:
    """Topology from an RTI Admin Console discovery data export -- File >
    Export Discovery Data (SCRUM-123). Schema confirmed on a real export from
    the bench (Admin Console 6.1.2.23, 2026-11-24):

        <system>
          <product_version>major/minor/release/revision</product_version>
          <serialization_date>...</serialization_date>
          <processes><value><element>                       one per process
            <domain_participants><value><element>             one per participant
              <domain_id/>
              <participant_data> participant_name/name, product_version,
                                 property/value/element (name, value) </participant_data>
              <publications><value><element><publication_data>
                  topic_name, type_name, publication_name/name, reliability/kind, durability/kind
              <subscriptions>... subscription_data, same fields (subscription_name)

    Values are XML element text, not attributes. The names found go only into
    the stored rti.json, never the UI summary (SCRUM-68). Any DTD or entity
    declaration is refused before parsing, so an uploaded file cannot drive
    entity expansion or external fetches."""
    if _XML_DTD.search(xml_bytes):
        raise PluginError(
            "rti: the uploaded XML declares a DTD or entities, which are not processed",
            safe_reason="The uploaded Admin Console file could not be read.",
        )
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise PluginError(
            "rti: the uploaded file is not valid XML",
            safe_reason="The uploaded Admin Console file is not valid XML.",
        ) from None

    participants: list[dict] = []
    # (domain_id, topic) -> {"type", "writers", "readers"}
    topics: dict[tuple, dict] = {}
    processes = root.findall("processes/value/element")
    for proc in processes:
        for dp in proc.iterfind("domain_participants/value/element"):
            pdata = dp.find("participant_data")
            domain = _int(dp, "domain_id")
            if domain is None:
                domain = _int(pdata, "domain_id")
            props = {}
            if pdata is not None:
                for prop in pdata.iterfind("property/value/element"):
                    k = _text(prop, "name")
                    if k:
                        props[k] = _text(prop, "value") or ""
            counts = {"writers": 0, "readers": 0}
            for section, sample, role in (("publications", "publication_data", "writers"),
                                          ("subscriptions", "subscription_data", "readers")):
                for ep in dp.iterfind(f"{section}/value/element/{sample}"):
                    counts[role] += 1
                    name = _text(ep, "topic_name")
                    if not name:
                        continue
                    t = topics.setdefault((domain, name), {"type": None, "writers": 0, "readers": 0})
                    t["type"] = t["type"] or _text(ep, "type_name")
                    t[role] += 1
            participants.append({
                "domain_id": domain,
                "name": _text(pdata, "participant_name/name"),
                "product_version": _version(pdata.find("product_version") if pdata is not None else None),
                "process_id": props.get("dds.sys_info.process_id"),
                "service": props.get("axone_service_friendlyname"),
                # The RTI Monitoring Library sets these when it is loaded in the
                # participant's process -- a hint that Monitor statistics exist.
                "monitoring_library": "rti/dds/monitor/version" in props,
                **counts,
            })

    topic_list = [
        {"domain_id": d, "topic": n, **v}
        for (d, n), v in sorted(topics.items(), key=lambda kv: (kv[0][0] is None, kv[0]))
    ]
    domains = sorted({p["domain_id"] for p in participants if p["domain_id"] is not None})
    return {
        "is_discovery_export": root.tag == "system",
        "export_version": _version(root.find("product_version")),
        "exported_at": _text(root, "serialization_date"),
        "processes": len(processes),
        "domains": domains,
        "participants": participants,
        "topics": topic_list,
        "topic_names": sorted({t["topic"] for t in topic_list}),
        "types": sorted({t["type"] for t in topic_list if t["type"]}),
        "writers": sum(p["writers"] for p in participants),
        "readers": sum(p["readers"] for p in participants),
    }


# RTI Monitor session models (com.rti.dds.monitor.system.*): the entity kinds a
# saved session holds, one serialized object each.
_MONITOR_MODELS = (
    "SystemModel", "HostModel", "ProcessModel", "DomainModel", "DomainParticipantModel",
    "PublisherModel", "SubscriberModel", "DataWriterModel", "DataReaderModel", "DomainTopicModel",
)
# Statuses RTI gives an entity that has no statistics of its own. Aggregates
# (host, topic, domain...) report THROUGH_AGGREGATION and carry no data either.
_NO_STATS = ("MONITORING_SUPPORTED_NO_DATA_AVAILABLE", "MONITORING_SUPPORTED_THROUGH_AGGREGATION",
             "MONITORING_NOT_SUPPORTED")


def summarize_monitor_session(ser_bytes: bytes) -> dict:
    """Entities and monitoring status from an RTI Monitor saved session (File >
    Save) -- SCRUM-123. Read with the pure parser in javaser.py, which never
    loads or runs Java code. Layout confirmed on a real session from the bench
    (Monitor 6.1.2.23, 2026-11-24): a version string, an int count, then that
    many Externalizable com.rti.dds.monitor.system.*Model objects. Each model
    writes an ordered list of values -- its 16-byte entity id (+1 byte), its
    MonitoringStatus enum, statistics queues, property names, and for topics,
    writers and readers the topic and type names. We read only values we can
    recognize by type and shape, never by fixed position, and skip RTI's data
    type descriptions (most of the file) so memory stays small. Names go only
    into rti.json, never the UI summary (SCRUM-68)."""
    try:
        items = javaser.read_stream(ser_bytes, skip_prefixes=("com.rti.dds.typecode.",))
    except javaser.JavaSerError as e:
        raise PluginError(
            f"rti: the uploaded Monitor session could not be read: {e}",
            safe_reason="The uploaded RTI Monitor file could not be read.",
        ) from None

    version = items[0] if items and isinstance(items[0], str) else None
    declared = (int.from_bytes(items[1], "big")
                if len(items) > 1 and isinstance(items[1], bytes) and len(items[1]) == 4 else None)
    entities: dict[str, int] = {}
    statuses: dict[str, int] = {}
    domains: set[int] = set()
    topics: set[tuple] = set()
    participants: list[str] = []
    hosts: list[str] = []
    for obj in items[2:]:
        if not (isinstance(obj, javaser.JavaObject)
                and obj.class_name.startswith("com.rti.dds.monitor.system.")
                and obj.simple_name in _MONITOR_MODELS):
            continue
        kind = obj.simple_name
        entities[kind] = entities.get(kind, 0) + 1
        values = [v for vals in obj.annotations.values() for v in vals]
        entity_id = next((v for v in values if isinstance(v, bytes) and len(v) == 17), None)
        status = next((v.constant for v in values if isinstance(v, javaser.JavaEnum)
                       and v.class_name.endswith(".MonitoringStatus")), None)
        if status:
            statuses[status] = statuses.get(status, 0) + 1
        # Plain names: strings that are not RTI property keys ("a/b/...").
        names = [v for v in values if isinstance(v, str) and v and "/" not in v]
        # Domain-scoped aggregates have ids "ffffffff.<domain>.…".
        if kind in ("DomainModel", "DomainTopicModel") and entity_id and entity_id[:4] == b"\xff" * 4:
            domain = int.from_bytes(entity_id[4:8], "big")
            domains.add(domain)
            if kind == "DomainTopicModel":
                typ = next((n for n in names if "::" in n), None)
                topic = names[names.index(typ) - 1] if typ and names.index(typ) > 0 else None
                if topic:
                    topics.add((domain, topic, typ))
        elif kind == "DomainParticipantModel" and names:
            participants.append(names[0])
        elif kind == "HostModel" and names:
            hosts.append(names[-1])

    with_stats = sum(n for s, n in statuses.items() if s not in _NO_STATS)
    return {
        "is_monitor_session": bool(version) and declared is not None and bool(entities),
        "monitor_version": version,
        "declared_entities": declared,
        "entities": dict(sorted(entities.items())),
        "domains": sorted(domains),
        "participants": sorted(participants),
        "hosts": sorted(set(hosts)),
        "topics": [{"domain_id": d, "topic": t, "type": ty} for d, t, ty in sorted(topics)],
        "topic_names": sorted({t for _, t, _ in topics}),
        "monitoring_status": dict(sorted(statuses.items())),
        "has_statistics": with_stats > 0,
    }


def summarize(spy_output: str) -> dict:
    """Topics from rtiddsspy's discovery lines: per topic its type and the
    writer/reader host ids present at the END of the capture. rtiddsspy reports a
    change code per record -- new/updated (+) or deleted (-) -- so a participant
    that is discovered and then deleted during the run is removed, and the
    summary reflects what was live when the run ended rather than everything ever
    seen. Sample contents are never matched.

    This is HOST PRESENCE per topic, not an endpoint count: the Src HostId names
    a host, and a host can run several participants/endpoints on the same topic.
    We track one presence per (topic, writer/reader, host), so a dispose ('-')
    for a host clears that host from the topic even if another endpoint on the
    same host is still live, and the host count is distinct hosts, not endpoints.
    Distinguishing endpoints would need the participant GUID, which this spy
    table does not carry; revisit only against a real multi-endpoint capture
    (SCRUM-39 follow-up review)."""
    topics: dict = {}
    for m in RTI_SPY_LINE.finditer(spy_output):
        t = topics.setdefault(m["topic"], {"type": m["type"], "writers": set(), "readers": set()})
        who = t["writers"] if m["kind"] == "W" else t["readers"]
        if m["change"] == "-":
            who.discard(m["host"])   # host presence, not per-endpoint: see docstring
        else:
            who.add(m["host"])
    topics = {name: t for name, t in topics.items() if t["writers"] or t["readers"]}
    hosts: set = set()
    for t in topics.values():
        hosts |= t["writers"] | t["readers"]
    return {
        "hosts": sorted(hosts),
        "topics": {
            name: {"type": t["type"], "writers": sorted(t["writers"]), "readers": sorted(t["readers"])}
            for name, t in sorted(topics.items())
        },
    }
