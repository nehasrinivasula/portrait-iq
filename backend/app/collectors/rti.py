"""
UMF RTI: message traffic between hub, viewer, and server (DDS/RTPS).

Per Andrew Welch's US-side note: this traffic is encrypted, so how much a
capture can show depends on what certificates the hospital allows. Surface
that as a warning to the field engineer, don't just silently return an
empty/useless capture.
"""
from app.collectors.base import Collector, CollectorResult
from app.collectors.registry import register


@register
class RtiCollector(Collector):
    name = "rti"
    display_name = "UMF RTI"
    description = "Captures DDS/RTPS message traffic between hub, viewer and server."

    def run(self, target_ip: str) -> CollectorResult:
        raise NotImplementedError(
            "TODO: deploy against the target and capture DDS/RTPS traffic. "
            "Remember: flag in CollectorResult.warnings if certificates "
            "aren't available, since encrypted payloads will limit what's visible."
        )

    def cleanup(self) -> None:
        raise NotImplementedError("TODO: tear down whatever run() deployed.")
