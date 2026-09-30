"""
Rules the analysis engine applies to normalized events. These 5 are the
actual patterns found in the 35 sample log files (548,303 rows, 1 Jun
20:00 - 3 Jun 06:59 UTC). Thresholds/baselines are from that one sample —
re-derive them once you have logs from more than one site or one window,
don't assume they generalize.

Every rule's `actions` are drafts, not confirmed fixes — the knowledge
layer is what would eventually replace these with a real manual match.
Keeping them here as a placeholder is a stopgap, not the intended end state.
"""
from dataclasses import dataclass
from typing import Callable

from app.models import NormalizedEvent


@dataclass
class Rule:
    id: str
    title_template: str          # "{n}" is replaced with the match count
    severity: str                  # "error" | "warning" | "info"
    match: Callable[[NormalizedEvent], bool]
    note: str
    draft_actions: list[str]


def _msg_has(*needles: str) -> Callable[[NormalizedEvent], bool]:
    def _match(e: NormalizedEvent) -> bool:
        return any(n in (e.message or "") for n in needles)
    return _match


RULES: list[Rule] = [
    Rule(
        id="ping_timeout",
        title_template="Ping scheduler times out ({n}x)",
        severity="error",
        match=lambda e: e.severity == "ERROR" and "ping scheduler" in (e.message or ""),
        note="Steady across all 35 sample hours (12/hour) — looks chronic, not a new incident.",
        draft_actions=[
            "Find which device or address the ping scheduler cannot reach",
            "Test that path with Netshoot (route, ports)",
            "Compare against a healthy site",
        ],
    ),
    Rule(
        id="cleanup_failed",
        title_template="Cleanup of Patient Data Recording files fails ({n}x)",
        severity="error",
        match=_msg_has("Cleanup of Patient Data Recording files"),
        note="Constant ~7/hour across 6 devices in the sample. Recordings may accumulate on disk.",
        draft_actions=[
            "Check free disk space on the affected devices",
            "Inspect the cleanup systemd unit's status",
            "Flag recorded files for the PHI/PII review",
        ],
    ),
    Rule(
        id="dds_security",
        title_template="DDS discovery rejects / auth time-outs ({n}x)",
        severity="warning",
        match=lambda e: e.severity == "WARN" and "!DDS" in (e.message or ""),
        note="Varies by hour in the sample, peaking around 04:00 UTC. Remote participants "
             "removed after authentication time-outs.",
        draft_actions=[
            "Check clock sync between devices and server",
            "Check certificates and security settings for the failing participant",
            "Check UDP multicast and firewall rules",
        ],
    ),
    Rule(
        id="hidepid",
        title_template="Kernel rejects 'hidepid' mount option ({n}x)",
        severity="warning",
        match=_msg_has("hidepid"),
        note="Single device in the sample. Looks like a config typo; confirm with engineering "
             "before assuming it's harmless.",
        draft_actions=[
            "Check the mount options in fstab",
            "Ask engineering if it's safe to ignore",
        ],
    ),
    Rule(
        id="healthcheck_noise",
        title_template="Successful health checks logged as warnings ({n}x)",
        severity="info",
        match=lambda e: e.severity == "WARN" and (
            "Healthcheck succeeded" in (e.message or "") or "Health check passed" in (e.message or "")
        ),
        note="~118/hour in the sample. Worth filtering by default so real warnings stand out.",
        draft_actions=["Ask engineering to log these at INFO instead of WARN"],
    ),
]
