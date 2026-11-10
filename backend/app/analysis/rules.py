"""
Pattern rules the analysis engine runs over the normalized event stream.

A rule is a name for a known issue plus one or more matchers. A matcher says which
event levels it applies to and which text must (and must not) be in the message.
Matching is case-insensitive and tolerant of the spellings seen in practice
("time-out", "timed out", "timeout"), because a rule that only knows one spelling
silently misses the others.

Every rule carries its own proof, in `examples` and `near_misses`:

  * `examples` are (level, message) pairs that MUST match. tests/test_analysis_rules.py
    runs every one, so a rule can't be added without at least one line it has to catch.
  * `near_misses` are pairs that must NOT match, which keeps a broad rule honest.

The first rules came from the 35 sample log files (548,303 rows, 1 Jun 20:00 - 3 Jun
06:59 UTC). Thresholds and baselines in the notes are from that one sample: re-derive
them once there are logs from more than one site or window.

Every rule's `draft_actions` are drafts, not confirmed fixes. The knowledge layer
(app/knowledge) is what would replace them with a real manual match.

To add a pattern: add a Rule below with at least one example, run the tests, then run
`python -m app.analysis.coverage <collection id>` on real data to see what ERROR and
WARN events still match nothing.
"""
import re
from dataclasses import dataclass
from typing import Optional

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"
PROBLEM_LEVELS = frozenset({ERROR, WARN})


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


@dataclass(frozen=True)
class Matcher:
    pattern: re.Pattern
    levels: Optional[frozenset] = None          # None: any level
    exclude: Optional[re.Pattern] = None        # if this also matches, the matcher does not

    def matches(self, level: str, message: str) -> bool:
        if self.levels is not None and level not in self.levels:
            return False
        if self.pattern.search(message) is None:
            return False
        return self.exclude is None or self.exclude.search(message) is None


@dataclass(frozen=True)
class Rule:
    id: str
    title_template: str                  # "{n}" is replaced with the match count
    severity: str                        # "error" | "warning" | "info"
    matchers: tuple
    note: str
    draft_actions: tuple
    examples: tuple                      # (level, message) pairs that must match
    near_misses: tuple = ()              # (level, message) pairs that must not

    def matches(self, level: str, message: Optional[str]) -> bool:
        text = message or ""
        return any(m.matches(level, text) for m in self.matchers)


_PING = r"\bping\b"
_TIMEOUT = r"time[ _-]?(?:d[ _-]?)?out"                  # timeout, time-out, timed out, time out


RULES: tuple = (
    Rule(
        id="ping_timeout",
        title_template="Ping scheduler times out ({n}x)",
        severity="error",
        matchers=(
            Matcher(_rx(r"ping[ _-]*scheduler"), levels=PROBLEM_LEVELS),
            Matcher(_rx(rf"{_PING}[^\r\n]{{0,80}}{_TIMEOUT}|{_TIMEOUT}[^\r\n]{{0,80}}{_PING}"), levels=PROBLEM_LEVELS),
            Matcher(_rx(rf"{_PING}[^\r\n]{{0,80}}(?:no (?:reply|response)|unreachable|not reachable|lost)"),
                    levels=PROBLEM_LEVELS),
        ),
        note="Steady across all 35 sample hours (12/hour) - looks chronic, not a new incident.",
        draft_actions=(
            "Find which device or address the ping scheduler cannot reach",
            "Test that path with Netshoot (route, ports)",
            "Compare against a healthy site",
        ),
        examples=(
            (ERROR, "Request/Response Timeout observed during the ping scheduler execution"),
            (ERROR, "ping scheduler timed out after 5000 ms"),
            (WARN, "Ping scheduler: no response from 10.0.0.5"),
            (WARN, "Ping to hub-a timed out"),
            (ERROR, "Timeout waiting for ping reply"),
            (ERROR, "PING_SCHEDULER failed"),
        ),
        near_misses=(
            (INFO, "ping scheduler started"),
            (ERROR, "Disk is full"),
            (WARN, "Mapping timeout reached"),            # "ping" inside another word
        ),
    ),
    Rule(
        id="request_timeout",
        title_template="Request/response time-outs not tied to ping ({n}x)",
        severity="warning",
        matchers=(
            Matcher(_rx(r"request[ /_-]*response[ _-]*" + _TIMEOUT), levels=PROBLEM_LEVELS, exclude=_rx(_PING)),
        ),
        note="Same wording as the ping time-out but without ping in the message, so it is counted "
             "separately: it may be the same cause or a different one.",
        draft_actions=(
            "Check which request is timing out from the surrounding events on the same device",
            "Test the path to the device with Netshoot",
        ),
        examples=(
            (ERROR, "Request/Response Timeout observed"),
            (WARN, "request response time-out while waiting for the hub"),
        ),
        near_misses=(
            (ERROR, "Request/Response Timeout observed during the ping scheduler execution"),   # counted as ping_timeout
            (INFO, "Request/Response Timeout observed"),
        ),
    ),
    Rule(
        id="cleanup_failed",
        title_template="Cleanup of Patient Data Recording files fails ({n}x)",
        severity="error",
        matchers=(
            Matcher(_rx(r"cleanup of patient data recording files"), levels=PROBLEM_LEVELS),
            Matcher(_rx(r"cleanup of patient data recording files[^\r\n]{0,80}(?:fail|error)")),   # at any level
        ),
        note="Constant ~7/hour across 6 devices in the sample. Recordings may accumulate on disk.",
        draft_actions=(
            "Check free disk space on the affected devices",
            "Inspect the cleanup systemd unit's status",
            "Flag recorded files for the PHI/PII review",
        ),
        examples=(
            (ERROR, "Cleanup of Patient Data Recording files failed"),
            (WARN, "Cleanup of Patient Data Recording files returned exit code 1"),
            (INFO, "Cleanup of Patient Data Recording files failed: disk busy"),
        ),
        near_misses=(
            (INFO, "Cleanup of Patient Data Recording files finished"),
            (ERROR, "Cleanup of temporary files failed"),
        ),
    ),
    Rule(
        id="dds_security",
        title_template="DDS discovery rejects / auth time-outs ({n}x)",
        severity="warning",
        matchers=(
            Matcher(_rx(r"!DDS"), levels=PROBLEM_LEVELS),
            Matcher(_rx(r"\bDDS\b[^\r\n]{0,100}(?:reject|authenticat|" + _TIMEOUT + r"|removed|fail|error|sample ?lost)"),
                    levels=PROBLEM_LEVELS),
            # on_sample_lost is the DDS DataReaderListener callback; some components log it without "DDS" in the line.
            Matcher(_rx(r"\bon[ _]?sample[ _]?lost\b"), levels=PROBLEM_LEVELS),
        ),
        note="Varies by hour in the sample, peaking around 04:00 UTC. Remote participants "
             "removed after authentication time-outs.",
        draft_actions=(
            "Check clock sync between devices and server",
            "Check certificates and security settings for the failing participant",
            "Check UDP multicast and firewall rules",
        ),
        examples=(
            (WARN, "!DDS DISCSimpleParticipantDiscoveryPluginReaderListener_onSampleLost: ff0101c7"),
            (WARN, "!DDS authentication time-out, remote participant removed"),
            (ERROR, "DDS participant rejected: security check failed"),
            (WARN, "dds discovery error on domain 0"),
            (WARN, "on_sample_lost: total_count=4"),
            (WARN, "listener onSampleLost called"),
        ),
        near_misses=(
            (INFO, "!DDS participant discovered"),
            (WARN, "Healthcheck succeeded in 12 ms"),
            (WARN, "Address book updated"),               # "dds" inside another word
            (INFO, "on_sample_lost listener registered"),
        ),
    ),
    Rule(
        id="hidepid",
        title_template="Kernel rejects 'hidepid' mount option ({n}x)",
        severity="warning",
        matchers=(Matcher(_rx(r"hidepid")),),
        note="Single device in the sample. Looks like a config typo; confirm with engineering "
             "before assuming it's harmless.",
        draft_actions=(
            "Check the mount options in fstab",
            "Ask engineering if it's safe to ignore",
        ),
        examples=(
            (WARN, "proc: Bad value for 'hidepid'"),
            (ERROR, "Unknown parameter 'hidepid'"),
            (INFO, "mount: hidepid=2 ignored"),
        ),
        near_misses=((WARN, "Process hidden by user"),),
    ),
    Rule(
        id="healthcheck_noise",
        title_template="Successful health checks logged as warnings ({n}x)",
        severity="info",
        matchers=(
            Matcher(_rx(r"health[ _-]?check[ _-]+(?:succeeded|passed|successful|ok)\b"), levels=frozenset({WARN})),
        ),
        note="~118/hour in the sample. Worth filtering by default so real warnings stand out.",
        draft_actions=("Ask engineering to log these at INFO instead of WARN",),
        examples=(
            (WARN, "Healthcheck succeeded in 12 ms"),
            (WARN, "Health check passed"),
            (WARN, "health-check OK"),
        ),
        near_misses=(
            (INFO, "Healthcheck succeeded in 12 ms"),
            (WARN, "Healthcheck failed"),
            (ERROR, "Health check passed with errors"),
        ),
    ),
)

RULES_BY_ID = {r.id: r for r in RULES}
