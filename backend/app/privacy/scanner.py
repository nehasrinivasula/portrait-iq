"""
Finds text in collected logs that may be PHI or PII, by known patterns.

Pure Python (no database or web imports) so the rules are easy to test and
can also be run over non-log output (netshoot / tshark text) by the packaging
step.

What a flag is: a span of one field (`message`, `device_id`, `process` or
`category`) that looks like a given kind of sensitive data. A flag records the
field, the kind (`category`), how sure the rule is (`confidence`) and the
character range, NEVER the matched text. That matters: the flags are stored
and returned by the API, and the audit log must not become a second copy of
the data they point at.

What this does NOT do:
  * It cannot find data with no recognisable shape. A bare name in free text
    ("John Smith disconnected") is not detected; a name after a label
    ("patientName=John Smith") is.
  * It does not remove anything. Flagging marks; mask_text() replaces flagged
    spans when a caller asks for a masked view.
  * Confidence "low" rules (IP and MAC addresses) are expected to include
    harmless matches, such as a software version that looks like an address.
    They are flagged because IP and MAC addresses are HIPAA identifiers and
    this tool exists to read network data, so they are worth a human look.

Bump SCANNER_VERSION whenever a rule changes. Events remember the version
that scanned them, so a rule change shows up as "needs rescanning" instead of
silently leaving old events judged by old rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Optional

SCANNER_VERSION = 1

# Fields of a log event that are scanned. `message` is where nearly all of it is.
FIELDS = ("message", "device_id", "process", "category")

HIGH, MEDIUM, LOW = "high", "medium", "low"
_RANK = {LOW: 0, MEDIUM: 1, HIGH: 2}

# What each category means, for the API and the S-3 documentation.
CATEGORY_LABELS = {
    "person_name": "Person name after a label such as patientName=",
    "record_identifier": "Patient, medical record, account or insurance number after a label",
    "date_of_birth": "Date of birth after a label such as dob=",
    "hl7_patient_segment": "HL7 patient, next-of-kin, guarantor or insurance segment (PID, NK1, GT1, IN1)",
    "ssn": "US Social Security number",
    "email": "Email address",
    "phone": "US phone number (with separators)",
    "credit_card": "Payment card number (passes the Luhn check)",
    "street_address": "US street address",
    "ip_address": "IPv4 address (HIPAA identifier; also normal network data)",
    "mac_address": "MAC address (HIPAA device identifier; also normal network data)",
}


@dataclass(frozen=True, slots=True)
class Flag:
    field: str
    category: str
    confidence: str
    start: int
    end: int

    def as_list(self) -> list:
        return [self.field, self.category, self.confidence, self.start, self.end]


@dataclass(frozen=True, slots=True)
class _Hit:
    category: str
    confidence: str
    start: int
    end: int


# --- rules ------------------------------------------------------------------

def _luhn_ok(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if flip:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        flip = not flip
    return total % 10 == 0


_SSN = re.compile(r"(?<![\w-])(\d{3})-(\d{2})-(\d{4})(?![\w-])")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")
# Separators are required between the groups: a bare 10-digit run is far more
# likely to be a counter or an id than a phone number.
_PHONE = re.compile(r"(?<![\w.-])(?:\+?1[ .-])?(?:\(\d{3}\)\s?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\w-])")
_CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")
_STREET = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][a-z]+\.?\s){1,3}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Way|Place|Pl)\b\.?"
)
_IPV4 = re.compile(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?![\w]|\.\d)")
_MAC = re.compile(r"(?<![\w:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![\w:-])")

# HL7 v2 segments that carry patient data. The segment ends at a carriage
# return, a newline, or their escaped two-character forms (\r, \n) as they
# appear inside single-line log messages.
_HL7 = re.compile(r"(?:^|(?<=[\s\"'|])|(?<=\\r)|(?<=\\n))(?:PID|NK1|GT1|IN1)\|[^\r\n]*?(?=\\r|\\n|\r|\n|$)")

# label -> category. The value after the label is what gets flagged.
_LABELS = {
    "person_name": r"patient[_ ]?name|pt[_ ]?name|first[_ ]?name|last[_ ]?name|given[_ ]?name|family[_ ]?name|surname|full[_ ]?name|guarantor[_ ]?name|next[_ ]?of[_ ]?kin",
    "record_identifier": r"patient[_ ]?id|patient[_ ]?number|mrn|medical[_ ]?record(?:[_ ]?(?:number|no|id))?|account[_ ]?(?:number|no|id)|insurance[_ ]?(?:id|number)|member[_ ]?id|policy[_ ]?(?:id|number)|visit[_ ]?(?:id|number)|encounter[_ ]?(?:id|number)",
    "date_of_birth": r"dob|date[_ ]?of[_ ]?birth|birth[_ ]?date|birthday",
    "street_address": r"home[_ ]?address|street[_ ]?address|mailing[_ ]?address|patient[_ ]?address",
    "ssn": r"ssn|social[_ ]?security(?:[_ ]?(?:number|no))?",
    "phone": r"patient[_ ]?phone|home[_ ]?phone|mobile[_ ]?phone|phone[_ ]?number",
    "email": r"patient[_ ]?email|e-?mail",
}
_LABEL_NAMES = [(cat, f"lbl_{i}") for i, cat in enumerate(_LABELS)]
_LABELED = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    + "|".join(f"(?P<{g}>{_LABELS[c]})" for c, g in _LABEL_NAMES)
    + r")(?![A-Za-z0-9])[\"']?\s*(?:=>|:|=)\s*"
    r"(?:\"(?P<dq>[^\"]{1,80})\"|'(?P<sq>[^']{1,80})'|(?P<bare>[^\s,;|\"'}\])]+(?:[ ](?-i:[A-Z][A-Za-z'.-]*)){0,2}))",   # extra words only if capitalised: a name, not a sentence
    re.IGNORECASE,
)
# Cheap pre-filter: the label rule only runs on text that contains a word any label needs.
_LABEL_WORDS = re.compile(
    r"patient|pt[_ ]?name|mrn|name|surname|kin|record|account|insurance|member|policy|visit|encounter"
    r"|dob|birth|ssn|social|phone|mail|address", re.IGNORECASE).search
_PLACEHOLDERS = {"", "null", "none", "nil", "n/a", "na", "unknown", "undefined", "empty",
                 "redacted", "masked", "-", "--", "?", "true", "false", "0", "<empty>", "[redacted]"}


def _hits_ssn(text: str) -> list[_Hit]:
    out = []
    for m in _SSN.finditer(text):
        area, group, serial = m.groups()
        if area in ("000", "666") or area[0] == "9" or group == "00" or serial == "0000":
            continue
        out.append(_Hit("ssn", HIGH, m.start(), m.end()))
    return out


def _hits_email(text: str) -> list[_Hit]:
    return [_Hit("email", HIGH, m.start(), m.end()) for m in _EMAIL.finditer(text)]


def _hits_phone(text: str) -> list[_Hit]:
    return [_Hit("phone", MEDIUM, m.start(), m.end()) for m in _PHONE.finditer(text)]


def _hits_card(text: str) -> list[_Hit]:
    out = []
    for m in _CARD.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())
        if 13 <= len(digits) <= 19 and digits[0] in "3456" and _luhn_ok(digits):
            out.append(_Hit("credit_card", MEDIUM, m.start(), m.end()))
    return out


def _hits_street(text: str) -> list[_Hit]:
    return [_Hit("street_address", MEDIUM, m.start(), m.end()) for m in _STREET.finditer(text)]


def _hits_ip(text: str) -> list[_Hit]:
    out = []
    for m in _IPV4.finditer(text):
        parts = m.group(1).split(".")
        if all(p.isdigit() and int(p) <= 255 and (p == "0" or not p.startswith("0")) for p in parts):
            out.append(_Hit("ip_address", LOW, m.start(1), m.end(1)))
    return out


def _hits_mac(text: str) -> list[_Hit]:
    return [_Hit("mac_address", LOW, m.start(), m.end()) for m in _MAC.finditer(text)]


def _hits_hl7(text: str) -> list[_Hit]:
    return [_Hit("hl7_patient_segment", HIGH, m.start(), m.end()) for m in _HL7.finditer(text)]


def _hits_labeled(text: str) -> list[_Hit]:
    out = []
    for m in _LABELED.finditer(text):
        category = next((c for c, g in _LABEL_NAMES if m.group(g)), None)
        for group in ("dq", "sq", "bare"):
            value = m.group(group)
            if value is not None:
                start, end = m.start(group), m.end(group)
                break
        else:
            continue
        stripped = value.strip()
        if stripped.lower() in _PLACEHOLDERS:
            continue
        if category != "person_name":
            # Only names run on across spaces; stop other values at the first space.
            value = stripped.split(" ")[0]
            end = start + len(value)
            if value.lower() in _PLACEHOLDERS:
                continue
        out.append(_Hit(category, HIGH, start, end))
    return out


# (cheap test that must be true for the rule to possibly match, rule)
_RULES: list[tuple[Callable[[str], bool], Callable[[str], list[_Hit]]]] = [
    (lambda t: "|" in t, _hits_hl7),
    (lambda t: (":" in t or "=" in t) and _LABEL_WORDS(t) is not None, _hits_labeled),
    (lambda t: "@" in t, _hits_email),
    (lambda t: "-" in t, _hits_ssn),
    (lambda t: "-" in t or "." in t or " " in t or "(" in t, _hits_phone),
    (lambda t: "-" in t or " " in t or len(t) >= 13, _hits_card),
    (lambda t: " " in t, _hits_street),
    (lambda t: "." in t, _hits_ip),
    (lambda t: ":" in t or "-" in t, _hits_mac),
]


def _resolve_overlaps(hits: list[_Hit]) -> list[_Hit]:
    """Two rules can claim the same text (a labeled phone number is also a
    phone number). Keep the most confident, then the longest; drop the rest so
    masking never works on overlapping ranges."""
    ordered = sorted(hits, key=lambda h: (-_RANK[h.confidence], -(h.end - h.start), h.start))
    kept: list[_Hit] = []
    for h in ordered:
        if all(h.end <= k.start or h.start >= k.end for k in kept):
            kept.append(h)
    return sorted(kept, key=lambda h: h.start)


def scan_text(text: Optional[str], field: str = "message") -> tuple[Flag, ...]:
    """Flags in one piece of text. Empty text has none."""
    if not text:
        return ()
    return _scan_cached(text, field)


@lru_cache(maxsize=8192)
def _scan_cached(text: str, field: str) -> tuple[Flag, ...]:
    # Logs repeat the same line thousands of times; the cache makes that free.
    hits: list[_Hit] = []
    for precheck, rule in _RULES:
        if precheck(text):
            hits.extend(rule(text))
    return tuple(Flag(field, h.category, h.confidence, h.start, h.end) for h in _resolve_overlaps(hits))


def scan_event_fields(*, message: Optional[str], device_id: Optional[str] = None,
                      process: Optional[str] = None, category: Optional[str] = None) -> list[Flag]:
    """Flags across every scanned field of one event."""
    flags: list[Flag] = list(scan_text(message, "message"))
    for name, value in (("device_id", device_id), ("process", process), ("category", category)):
        flags.extend(scan_text(value, name))
    return flags


# --- storing flags ------------------------------------------------------------
# Events keep their flags as a compact JSON list of
# [field, category, confidence, start, end]. None means "scanned, nothing found".

def encode_flags(flags: list[Flag]) -> Optional[str]:
    import json
    return json.dumps([f.as_list() for f in flags], separators=(",", ":")) if flags else None


def decode_flags(raw: Optional[str]) -> list[Flag]:
    import json
    if not raw:
        return []
    try:
        return [Flag(str(f), str(c), str(k), int(s), int(e)) for f, c, k, s, e in json.loads(raw)]
    except (ValueError, TypeError):
        return []   # a damaged value must not break reading the log; the report counts it as flagged


def mask_text(text: Optional[str], flags: list[Flag], field: str = "message") -> Optional[str]:
    """Replace each flagged span of `field` with [REDACTED:<category>]."""
    if not text:
        return text
    spans = sorted((f for f in flags if f.field == field and 0 <= f.start < f.end <= len(text)),
                   key=lambda f: f.start)
    if not spans:
        return text
    out, pos = [], 0
    for f in spans:
        if f.start < pos:
            continue
        out.append(text[pos:f.start])
        out.append(f"[REDACTED:{f.category}]")
        pos = f.end
    out.append(text[pos:])
    return "".join(out)
