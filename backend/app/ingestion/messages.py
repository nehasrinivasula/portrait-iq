"""
Free-text system message parsing: turns "General" log lines into structured
request / reply / publish facts so exchanges can be followed and checked.

Where the information comes from (profiled on the 35-file sample, 548,303
rows, 174,744 of them "General"):

  component       the Java class from sourceInfo, e.g.
                  "com.ge.hc.lcs.axone.configservice.message.ConfigMessageProcessor:478"
                  -> "ConfigMessageProcessor". (The ticket says "processInfo
                  class path"; processInfo only holds "name[pid:thread]", the
                  class path is in sourceInfo.) The log export API's format
                  "StartupService.java:88" -> "StartupService".
  message_kind    request | reply | response | publish | timeout
  command         what was asked for, e.g. ChangeConfigCmd, cmd://cmd/util/ping
  correlation_id  the id that ties a request to its reply (uuidRequest, reqId,
                  responseuuid, stream id), so a sequence can be rebuilt
  status          the outcome a reply reports, e.g. "ack", "nak_alreadyExists",
                  "500", "SUCCESS"

The rule table below covers every request/reply/publish shape seen in the
sample (26 shapes). Anything else that clearly talks about a request, reply,
publish or timeout still gets a message_kind from a keyword fallback, without
the other fields. The timeout fallback only applies to WARN and ERROR lines, so
an INFO "timeout set to 30 s" is not a timeout.

Privacy: these fields are not PHI-scanned at ingest, so nothing free-form may
reach them. Every rule captures a bounded shape (a command name, a cmd:// route
without digits, one status word or a 3-digit code, a UUID), and classify()
drops any command or status that does not fit those shapes. Patient ids,
names, paths and response bodies on a line are never copied into these fields.

Each rule also says how its requests are paired with replies (`PAIRING`), so
storage/sequences.py does not report e.g. a licence health check that never
logs a reply as "unanswered".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.ingestion.schema import MESSAGE_KINDS as KINDS  # noqa: F401  (one list for both)

# How storage/sequences.py pairs a component's requests with replies:
#   "id"    by correlation_id
#   "next"  with the next reply from the same device + component (no id logged)
# Components not listed log requests that are never answered in the logs, so
# they are not turned into sequences at all.
PAIRING = {
    "ConfigMessageProcessor": "id",
    "QueryWorker": "id",
    "Requester$RespDispatchRunner": "id",
    "HealthCheckRequestCallback": "next",
}

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_CMD_URI = r"cmd://[A-Za-z_/.\-]+"           # no digits: routes, never record ids
# The only shapes command and status may have (see "Privacy" above).
_SAFE_COMMAND = re.compile(r"^(?:-?[A-Za-z][\w-]{0,60}(?: [A-Za-z][\w-]{0,30}){0,3}|cmd://[A-Za-z_/.\-]{1,80})$")
_SAFE_STATUS = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_]{0,40}|\d{3})$")
# Cheap pre-check: skip the rule table for lines that can't match anything.
_HINT = re.compile(r"request|reply|response|respond|publish|timeout|timed[- ]out", re.IGNORECASE)


@dataclass(frozen=True)
class MessageInfo:
    kind: str
    command: Optional[str] = None
    correlation_id: Optional[str] = None
    status: Optional[str] = None


_SOURCE_FILE_EXT = re.compile(r"\.(java|cpp|cc|cxx|c|h|hpp|py|kt)$", re.IGNORECASE)


def component_from_source_info(source_info: Optional[str]) -> Optional[str]:
    """Class or source-file name from sourceInfo:
    'pkg.Outer$Inner:12' -> 'Outer$Inner' (Java class path),
    'DdsLogProcess.cpp:88' / 'StartupService.java:88' -> the file's base name
    (C++ services and the log export API), ':0' or blank -> None."""
    text = (source_info or "").strip()
    if not text:
        return None
    path = text.rsplit(":", 1)[0] if ":" in text else text
    path = path.rsplit("/", 1)[-1]
    if _SOURCE_FILE_EXT.search(path):
        return _SOURCE_FILE_EXT.sub("", path) or None
    name = path.rsplit(".", 1)[-1].strip()
    return name or None


def _status(raw: Optional[str]) -> Optional[str]:
    """'Ea52_ack' -> 'ack', 'Ea52_nak_alreadyExists' -> 'nak_alreadyExists'."""
    if not raw:
        return None
    return re.sub(r"^Ea\d+_", "", raw.strip())


@dataclass(frozen=True)
class _Rule:
    component: Optional[str]      # None = any component
    pattern: re.Pattern
    kind: str
    command: Optional[str] = None  # fixed command when the line has none


def _rule(component, pattern, kind, command=None):
    return _Rule(component, re.compile(pattern), kind, command)


# Ordered: the first rule whose component and pattern match wins. Named groups
# cmd / corr / status fill the fields.
_RULES = [
    # Config service: request -> reply by uuidRequest, plus config publishes.
    _rule("ConfigMessageProcessor", rf"RECEIVED: Request - uuidRequest: (?P<corr>{_UUID}), (?P<cmd>\w+)", "request"),
    _rule("ConfigMessageProcessor",
          rf"SENDING: Reply - +uuidRequest: (?P<corr>{_UUID}), (?P<cmd>\w+), result: +status\((?P<status>[^)]+)\)", "reply"),
    _rule("ConfigMessageProcessor", r"PUBLISHING: (?P<cmd>\w+)", "publish"),
    # Persistence queries: request keyed by responseuuid, answered by a line that starts with it.
    _rule("QueryWorker", rf"responseuuid \.*(?P<corr>{_UUID})REQUEST: (?P<cmd>{_CMD_URI})", "request"),
    _rule("QueryWorker", rf"^(?P<corr>{_UUID}) - result data response size", "response", "query result"),
    # A reply that arrived with no request waiting for it.
    _rule("Requester$RespDispatchRunner",
          rf"unexpected RESPONSE: (?P<cmd>{_CMD_URI}).*?status:(?P<status>\w+).*?reqId:(?P<corr>{_UUID})", "response"),
    # Ping scheduler timeouts (several devices per line; no single id).
    _rule("PingRequestHandler", r"Timeout observed|ReqReplyTimeoutException", "timeout", "cmd://cmd/util/ping"),
    # HL7 outbound health check: request then "Successfully responded", no id.
    _rule("HealthCheckRequestCallback", r"^Received health-check request", "request", "health-check"),
    _rule("HealthCheckRequestCallback", r"^Successfully responded to .*health-check request", "reply", "health-check"),
    # Requests that never log a reply.
    _rule("LicenseManager", r"Starting the Healthcheck request", "request", "health-check"),
    # The resource path can name a patient, so only the fact of a download is kept.
    _rule("ResourceDownloadRequestTask", r"Request received for resource:", "request", "resource download"),
    # Trend data publish, keyed by stream id. The patient id on the line is NOT captured.
    _rule("SyncCondition", rf"Start publishing the data with stream id: (?P<corr>{_UUID})", "publish", "trend data"),
    # External calls that log their response status.
    # Only the HTTP code: the rest of the line can be a response body.
    _rule("SiteConfigAPIClient", r"Response Status for fetching Site Configs : (?P<status>\d{3})\b", "response",
          "fetching Site Configs"),
    _rule("OSUpdateManager", r"Socket Response for Input : (?P<cmd>-\w+ \w+).*Response : (?P<status>\w+)", "response"),
]

# Fallback for shapes not in the table yet: whole words only, so class names
# such as SyslogRequestHandler do not count.
_FALLBACK = [
    (re.compile(r"\btime[d]?[- ]?out\b|\btimeout\b", re.IGNORECASE), "timeout"),
    (re.compile(r"\breply\b|\breplied\b", re.IGNORECASE), "reply"),
    (re.compile(r"\bresponse\b|\bresponded\b", re.IGNORECASE), "response"),
    (re.compile(r"\bpublish(?:ing|ed)?\b", re.IGNORECASE), "publish"),
    (re.compile(r"\brequests?\b", re.IGNORECASE), "request"),
]


def _bounded(value: Optional[str], shape: re.Pattern) -> Optional[str]:
    return value if value and shape.match(value) else None


def classify(message: Optional[str], component: Optional[str],
             severity: Optional[str] = None) -> Optional[MessageInfo]:
    """What kind of exchange this line records, or None for ordinary lines.
    `severity` limits the timeout keyword fallback to WARN and ERROR lines."""
    text = message or ""
    if not _HINT.search(text):
        return None
    for rule in _RULES:
        if rule.component is not None and rule.component != component:
            continue
        m = rule.pattern.search(text)
        if m is None:
            continue
        groups = m.groupdict()
        return MessageInfo(
            kind=rule.kind,
            command=_bounded(groups.get("cmd") or rule.command, _SAFE_COMMAND),
            correlation_id=(groups.get("corr") or "").lower() or None,
            status=_bounded(_status(groups.get("status")), _SAFE_STATUS),
        )
    for pattern, kind in _FALLBACK:
        if kind == "timeout" and severity not in ("WARN", "ERROR"):
            continue
        if pattern.search(text):
            return MessageInfo(kind=kind)
    return None
