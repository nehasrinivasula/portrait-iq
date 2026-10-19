"""
Hub logs are NOT collected automatically. Per Andrew Welch and Francisco
Vega (US/EU network teams): pulling a hub log means physically discharging
the patient and switching the hub to service mode. This is a deliberate,
manual, field-engineer-triggered step — never something a scheduled or
automated job should attempt on its own.

This "collector" is really just an upload endpoint: a field engineer who
has already pulled a hub log by hand hands the file to Portrait IQ so it
can be normalized and joined with everything else.

Future: Francisco mentioned the hub's charger may eventually be able to
push logs over Ethernet without taking the device out of service. If that
becomes real, it plugs in here as a second, non-manual mode — this file
is where that change would live, not a new top-level collector.
"""
from app.collectors.base import Collector, CollectorResult
from app.collectors.registry import register


@register
class HubImportCollector(Collector):
    name = "hub_import"
    display_name = "Hub log import"
    description = "Accepts a hub log a field engineer already pulled by hand."

    def run(self, target_ip: str) -> CollectorResult:
        raise NotImplementedError(
            "TODO: accept an uploaded hub log file (from a field engineer who "
            "already pulled it manually), validate it's a recognized hub log "
            "format, and hand it to the ingestion pipeline. No network call here."
        )

    def cleanup(self) -> None:
        # No live resources to tear down for an upload-only path.
        return None
