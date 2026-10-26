"""
Wireshark/tshark. Two modes, per the EU network team's feedback (Andrew
Welch: "sometimes we get pushback from hospitals not allowing us to run
Wireshark ... you need to build in something that can read in the capture
rather than run it directly"):

  - "import": parse a .pcap/.pcapng someone else (usually hospital IT)
              already collected, which the field engineer uploaded with
              POST /api/uploads?kind=pcap. Needs no cluster.
  - "run":    capture on the node from a short-lived pod. That pod needs
              host networking and raw-socket rights, the most access any
              plugin asks for, so this mode is refused unless
              TSHARK_LIVE_CAPTURE_ENABLED is set for an approved site.

Capture headers only by default. Full payload capture can contain PHI/PII,
per the requirements doc's privacy section, so turning it on
(headers_only: false) requires a full_payload_reason, and both go in the
job_start audit entry. Headers only keeps the first HEADER_SNAPLEN bytes of
each packet: tshark's -s in run mode, editcap -s on the stored copy in
import mode. A short packet can still carry a few bytes of payload, so this
lowers the PHI/PII risk without removing it (S-3).

Either way the capture ends up in the job folder and tshark -r summarizes it
(protocol hierarchy, IP conversations), so tshark and editcap must be
installed where the backend runs.

What a live capture can see: traffic to and from the node it runs on, plus
broadcast and multicast. The lab switch is unmanaged and mirrors nothing,
so traffic between two other devices (e.g. hub to viewer) is not in it.
"""
import re
import shutil
import subprocess
from pathlib import Path

from app import uploads
from app.collectors import k8s
from app.collectors.base import Collector, CollectorResult, PluginConfigError, PluginError
from app.collectors.rti import rtps_port_range
from app.config import settings
from app.secrets_loader import redact

VISIBILITY_WARNING = (
    "A live capture only sees traffic to and from the node it ran on, plus broadcast and "
    "multicast. Traffic between other devices is missing unless the switch mirrors it."
)
ANALYZE_TIMEOUT_S = 300


def _rti_filter(*domains: int) -> str:
    return " or ".join("udp portrange %d-%d" % rtps_port_range(d) for d in domains)


# Named capture filters, so the UI offers choices instead of GE's port
# numbers. Same at every site: the ports follow from the UMF DDS domains.
FILTER_PRESETS = {
    "rti_all": _rti_filter(7, 12, 13),
    "rti_logging": _rti_filter(7),
    "rti_source_pod": _rti_filter(12),
    "rti_view_pod": _rti_filter(13),
}


class TsharkCollector(Collector):
    name = "tshark"
    display_name = "Wireshark / tshark"
    description = "Runs a packet capture, or imports a .pcap someone else collected."

    MODES = ("run", "import")
    MAX_REASON_LENGTH = 500   # a sentence or a ticket reference, not a document
    HEADER_SNAPLEN = 128      # bytes kept per packet when headers_only
    DEFAULT_DURATION_S = 60
    MAX_DURATION_S = 900
    INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$")
    # Capture filter (BPF): words, numbers, addresses and operators only.
    FILTER_PATTERN = re.compile(r"^[A-Za-z0-9 .:/()!=<>&|\[\]-]{1,200}$")

    COMMON_OPTIONS = {"mode", "headers_only", "full_payload_reason"}
    RUN_OPTIONS = {"duration_s", "interface", "capture_filter", "filter_preset"}
    IMPORT_OPTIONS = {"upload_id"}

    def __init__(self, limits):
        super().__init__(limits)
        self.mode = "run"
        self.headers_only = True
        self.full_payload_reason = ""
        self.duration_s = self.DEFAULT_DURATION_S
        self.interface = "any"
        self.capture_filter = ""
        self.filter_preset = ""
        self.upload_id = ""
        self._runner = None
        self._pod: str | None = None

    def configure(self, options):
        """Options: mode ("run" | "import", default "run"),
        headers_only (bool, default True) and full_payload_reason (text,
        required when headers_only is false, not allowed otherwise).
        Run mode: duration_s (default 60, at most 900), interface (default
        "any") and exactly one of filter_preset (a FILTER_PRESETS name) or
        capture_filter (BPF). Import mode: upload_id
        (required, from POST /api/uploads?kind=pcap)."""
        unknown = sorted(set(options) - self.COMMON_OPTIONS - self.RUN_OPTIONS - self.IMPORT_OPTIONS)
        if unknown:
            raise PluginConfigError(f"tshark does not accept options: {unknown}")
        mode = options.get("mode", "run")
        if mode not in self.MODES:
            raise PluginConfigError(f"tshark mode must be one of {self.MODES}; got {mode!r}")
        wrong = sorted(set(options) & (self.IMPORT_OPTIONS if mode == "run" else self.RUN_OPTIONS))
        if wrong:
            raise PluginConfigError(f"tshark {mode} mode does not take: {wrong}")
        headers_only = options.get("headers_only", True)
        if not isinstance(headers_only, bool):
            raise PluginConfigError("tshark headers_only must be true or false")
        reason = options.get("full_payload_reason", "")
        if not isinstance(reason, str):
            raise PluginConfigError("tshark full_payload_reason must be text")
        reason = reason.strip()
        if len(reason) > self.MAX_REASON_LENGTH:
            raise PluginConfigError(
                f"tshark full_payload_reason is limited to {self.MAX_REASON_LENGTH} characters"
            )
        if not headers_only and not reason:
            raise PluginConfigError(
                "tshark full payload capture can contain PHI/PII; "
                "give a full_payload_reason to turn headers_only off"
            )
        if headers_only and reason:
            raise PluginConfigError("tshark full_payload_reason only applies when headers_only is false")

        duration_s, interface, capture_filter, upload_id = self.DEFAULT_DURATION_S, "any", "", ""
        preset = ""
        if mode == "run":
            if not settings.tshark_live_capture_enabled:
                raise PluginConfigError(
                    "tshark live capture is turned off here: it needs a host-network pod with "
                    "raw-socket rights on the customer's node, which the site must approve. "
                    "Use mode 'import' with a capture from hospital IT instead."
                )
            duration_s = options.get("duration_s", self.DEFAULT_DURATION_S)
            if (not isinstance(duration_s, int) or isinstance(duration_s, bool)
                    or not 5 <= duration_s <= self.MAX_DURATION_S):
                raise PluginConfigError(
                    f"tshark duration_s must be a whole number of seconds from 5 to {self.MAX_DURATION_S}"
                )
            interface = options.get("interface", "any")
            if not isinstance(interface, str) or not self.INTERFACE_PATTERN.match(interface):
                raise PluginConfigError("tshark interface must be an interface name such as eth0 or any")
            # A filter is required: a live capture never takes everything on
            # the customer's network, which could include logins and PHI.
            if ("filter_preset" in options) == ("capture_filter" in options):
                raise PluginConfigError(
                    f"tshark live capture needs exactly one of filter_preset "
                    f"({', '.join(FILTER_PRESETS)}) or capture_filter"
                )
            if "filter_preset" in options:
                preset = options["filter_preset"]
                if not isinstance(preset, str) or preset not in FILTER_PRESETS:
                    raise PluginConfigError(
                        f"tshark filter_preset must be one of: {', '.join(FILTER_PRESETS)}"
                    )
                capture_filter = FILTER_PRESETS[preset]
            else:
                capture_filter = options["capture_filter"]
                if not isinstance(capture_filter, str):
                    raise PluginConfigError("tshark capture_filter must be text")
                capture_filter = capture_filter.strip()
                if not self.FILTER_PATTERN.match(capture_filter):
                    raise PluginConfigError(
                        "tshark capture_filter may only use letters, digits, spaces and "
                        ". : / ( ) ! = < > & | [ ] -"
                    )
        else:
            upload_id = options.get("upload_id")
            if not uploads.is_valid_id(upload_id):
                raise PluginConfigError(
                    "tshark import mode needs the upload_id returned by POST /api/uploads?kind=pcap"
                )
            if not uploads.exists(upload_id, "pcap"):
                raise PluginConfigError("tshark: no uploaded capture with that upload_id")

        self.mode = mode
        self.headers_only = headers_only
        self.full_payload_reason = reason
        self.duration_s, self.interface, self.capture_filter = duration_s, interface, capture_filter
        self.filter_preset = preset
        self.upload_id = upload_id
        self.options = {"mode": mode, "headers_only": headers_only, "full_payload_reason": reason}
        if mode == "run":
            self.options.update(duration_s=duration_s, interface=interface, capture_filter=capture_filter)
            if preset:
                self.options["filter_preset"] = preset
        else:
            self.options.update(upload_id=upload_id)

    def audit_fields(self):
        fields = {"mode": self.mode, "headers_only": self.headers_only}
        if not self.headers_only:
            # Free text from the user: mask anything that looks like a secret.
            fields["full_payload_reason"] = redact(self.full_payload_reason)
        if self.mode == "run":
            fields.update(duration_s=self.duration_s, interface=self.interface)
            if self.filter_preset:
                fields["filter_preset"] = self.filter_preset
            else:
                fields["capture_filter"] = redact(self.capture_filter)
        else:
            fields["upload_id"] = self.upload_id
        return fields

    def run(self, target_ip: str) -> CollectorResult:
        out_dir = self.require_output_dir()
        capture = out_dir / "capture.pcapng"
        warnings = []
        if self.mode == "import":
            self._import(capture)
        else:
            self._capture(capture)
            warnings.append(VISIBILITY_WARNING)

        summary_file = out_dir / "summary.txt"
        stats = _wireshark_tool(
            "tshark", ["-n", "-r", str(capture), "-q", "-z", "io,phs", "-z", "conv,ip"], ANALYZE_TIMEOUT_S
        )
        summary_file.write_text(stats)
        match = re.search(r"frames:(\d+)", stats)
        packets = int(match.group(1)) if match else 0
        if packets == 0:
            warnings.append("The capture has no packets: check the interface and capture filter.")
        how = "imported" if self.mode == "import" else f"captured over {self.duration_s}s"
        return CollectorResult(
            tool=self.name,
            artifact_paths=[str(capture), str(summary_file)],
            summary=f"{packets} packets {how}, {'headers only' if self.headers_only else 'full payload'}",
            warnings=warnings,
        )

    def _import(self, dest: Path) -> None:
        src = uploads.path_for(self.upload_id, "pcap")
        if not src.is_file():
            raise PluginError("tshark: the uploaded capture is gone; upload it again")
        size_mb = src.stat().st_size / (1024 * 1024)
        if size_mb > self.limits.max_output_mb:
            raise PluginError(
                f"tshark: the capture is {size_mb:.0f} MB, over this job's {self.limits.max_output_mb} MB limit"
            )
        if self.headers_only:
            _wireshark_tool(
                "editcap", ["-F", "pcapng", "-s", str(self.HEADER_SNAPLEN), str(src), str(dest)],
                ANALYZE_TIMEOUT_S,
            )
        else:
            _wireshark_tool("editcap", ["-F", "pcapng", str(src), str(dest)], ANALYZE_TIMEOUT_S)

    def _capture(self, dest: Path) -> None:
        if self._pod:            # run() again after a partial failure: start clean
            self.cleanup()
        start_s = settings.tool_pod_start_timeout_seconds
        deadline = start_s + self.duration_s + 300   # start, capture, copy out
        max_mb = self.limits.max_output_mb
        self._runner = k8s.get_pod_runner()
        self._pod = k8s.pod_name(self.name, self.job_id)   # set first, so cleanup() finds it
        self._runner.create(
            name=self._pod, tool=self.name, job_id=self.job_id,
            image=settings.tshark_image, command=["sleep", str(deadline)],
            deadline_s=deadline, limits=self.limits, host_network=True,
            add_capabilities=("NET_RAW", "NET_ADMIN"), tmp_size_mb=max_mb + 16,
        )
        self._runner.wait_running(self._pod, start_s)

        argv = [
            "tshark", "-i", self.interface, "-n", "-q",
            "-a", f"duration:{self.duration_s}", "-a", f"filesize:{max_mb * 1024}",
            "-w", "/tmp/capture.pcapng",
        ]
        if self.headers_only:
            argv += ["-s", str(self.HEADER_SNAPLEN)]
        if self.capture_filter:
            argv += ["-f", self.capture_filter]
        res = self._runner.exec(self._pod, argv, self.duration_s + 60)
        if res.exit_code != 0:
            why = "timed out" if res.exit_code is None else f"exit {res.exit_code}"
            last = redact(res.stderr.strip().splitlines()[-1][:200]) if res.stderr.strip() else ""
            raise PluginError(f"tshark capture failed ({why}) {last}".strip())
        self._runner.read_file(self._pod, "/tmp/capture.pcapng", dest, max_bytes=(max_mb + 1) * 1024 * 1024)

    def cleanup(self) -> None:
        if self._pod and self._runner:
            self._runner.delete(self._pod)
        self._pod = None


def _wireshark_tool(name: str, args: list[str], timeout_s: int) -> str:
    """Run tshark or editcap on the backend host; returns stdout."""
    exe = shutil.which(name, path=settings.wireshark_bin_dir) or shutil.which(name)
    if exe is None:
        raise PluginError(
            f"{name} is not installed where Portrait IQ runs. Install Wireshark's command-line "
            "tools (Ubuntu: apt install tshark; macOS: brew install wireshark; Windows: the "
            "Wireshark installer, then set WIRESHARK_BIN_DIR)."
        )
    try:
        proc = subprocess.run(
            [exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise PluginError(f"{name} took longer than {timeout_s}s") from None
    if proc.returncode != 0:
        last = proc.stderr.strip().splitlines()[-1][:200] if proc.stderr.strip() else ""
        raise PluginError(f"{name} failed (exit {proc.returncode}) {redact(last)}".strip())
    return proc.stdout
