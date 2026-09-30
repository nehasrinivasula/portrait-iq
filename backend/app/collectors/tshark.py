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
from app.collectors.base import Collector, CollectorResult


class TsharkCollector(Collector):
    name = "tshark"

    def __init__(self, limits, mode: str = "run", headers_only: bool = True):
        super().__init__(limits)
        self.mode = mode              # "run" | "import"
        self.headers_only = headers_only

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
