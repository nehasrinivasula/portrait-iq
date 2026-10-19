"""
Wireshark/tshark. Two modes, per the EU network team's feedback (Andrew
Welch: "sometimes we get pushback from hospitals not allowing us to run
Wireshark ... you need to build in something that can read in the capture
rather than run it directly"):

  - "run":    capture packets directly (needs permission on site)
  - "import": accept a .pcap/.pcapng someone else (usually hospital IT)
              already collected, and just parse it

Also: capture headers only by default. Full payload capture can contain
PHI/PII, per the requirements doc's privacy section — don't flip that
default without an explicit, logged reason.
"""
from app.collectors.base import Collector, CollectorResult, PluginConfigError
from app.collectors.registry import register


@register
class TsharkCollector(Collector):
    name = "tshark"
    display_name = "Wireshark / tshark"
    description = "Runs a packet capture, or imports a .pcap someone else collected."

    MODES = ("run", "import")

    def __init__(self, limits):
        super().__init__(limits)
        self.mode = "run"
        self.headers_only = True

    def configure(self, options):
        """Options: mode ("run" | "import", default "run") and
        headers_only (bool, default True)."""
        unknown = sorted(set(options) - {"mode", "headers_only"})
        if unknown:
            raise PluginConfigError(f"tshark does not accept options: {unknown}")
        mode = options.get("mode", "run")
        if mode not in self.MODES:
            raise PluginConfigError(f"tshark mode must be one of {self.MODES}; got {mode!r}")
        headers_only = options.get("headers_only", True)
        if not isinstance(headers_only, bool):
            raise PluginConfigError("tshark headers_only must be true or false")
        self.mode = mode
        self.headers_only = headers_only
        self.options = {"mode": mode, "headers_only": headers_only}

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
