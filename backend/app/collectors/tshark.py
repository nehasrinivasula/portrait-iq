"""
Wireshark/tshark. Two modes, per the EU network team's feedback (Andrew
Welch: "sometimes we get pushback from hospitals not allowing us to run
Wireshark ... you need to build in something that can read in the capture
rather than run it directly"):

  - "run":    capture packets directly (needs permission on site)
  - "import": accept a .pcap/.pcapng someone else (usually hospital IT)
              already collected, and just parse it

Also: capture headers only by default. Full payload capture can contain
PHI/PII, per the requirements doc's privacy section, so turning it on
(headers_only: false) requires a full_payload_reason, and both go in the
job_start audit entry.
"""
from app.collectors.base import Collector, CollectorResult, PluginConfigError
from app.secrets_loader import redact


class TsharkCollector(Collector):
    name = "tshark"
    display_name = "Wireshark / tshark"
    description = "Runs a packet capture, or imports a .pcap someone else collected."

    MODES = ("run", "import")

    def __init__(self, limits):
        super().__init__(limits)
        self.mode = "run"
        self.headers_only = True
        self.full_payload_reason = ""

    def configure(self, options):
        """Options: mode ("run" | "import", default "run"),
        headers_only (bool, default True) and full_payload_reason (text,
        required when headers_only is false, not allowed otherwise)."""
        unknown = sorted(set(options) - {"mode", "headers_only", "full_payload_reason"})
        if unknown:
            raise PluginConfigError(f"tshark does not accept options: {unknown}")
        mode = options.get("mode", "run")
        if mode not in self.MODES:
            raise PluginConfigError(f"tshark mode must be one of {self.MODES}; got {mode!r}")
        headers_only = options.get("headers_only", True)
        if not isinstance(headers_only, bool):
            raise PluginConfigError("tshark headers_only must be true or false")
        reason = options.get("full_payload_reason", "")
        if not isinstance(reason, str):
            raise PluginConfigError("tshark full_payload_reason must be text")
        reason = reason.strip()
        if not headers_only and not reason:
            raise PluginConfigError(
                "tshark full payload capture can contain PHI/PII; "
                "give a full_payload_reason to turn headers_only off"
            )
        if headers_only and reason:
            raise PluginConfigError("tshark full_payload_reason only applies when headers_only is false")
        self.mode = mode
        self.headers_only = headers_only
        self.full_payload_reason = reason
        self.options = {"mode": mode, "headers_only": headers_only, "full_payload_reason": reason}

    def audit_fields(self):
        fields = {"mode": self.mode, "headers_only": self.headers_only}
        if not self.headers_only:
            # Free text from the user: mask anything that looks like a secret.
            fields["full_payload_reason"] = redact(self.full_payload_reason)
        return fields

    def run(self, target_ip: str) -> CollectorResult:
        if self.mode == "import":
            raise NotImplementedError(
                "TODO: accept an uploaded .pcap/.pcapng, validate it, parse with "
                "pyshark or tshark -r, and normalize into events — no live capture."
            )
        raise NotImplementedError(
            f"TODO: run a live capture against {target_ip} "
            f"({'headers only' if self.headers_only else 'full payload — confirm this is intended'})."
        )

    def cleanup(self) -> None:
        raise NotImplementedError("TODO: stop capture process / delete pod if 'run' mode was used.")
