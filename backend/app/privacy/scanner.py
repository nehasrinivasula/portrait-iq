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

SCANNER_VERSION = 3

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


# IP and MAC addresses identify equipment, which is what this tool exists to read: device ids here
# are often MAC-style, and peer lists are full of addresses. They are recorded, but apart from
# the personal kinds, and they do not count as "flagged".
NETWORK_CATEGORIES = frozenset({"ip_address", "mac_address"})


def category_group(category: str) -> str:
    return "network" if category in NETWORK_CATEGORIES else "personal"


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
_CARD = re.compile(r"(?<![\w.-])(?:\d[ -]?){12,18}\d(?![\w-])")
# "123 Main St", "123 N Main St", "456 W 5th Ave", "123 MAIN ST". Name words are capitalised (a single
# letter such as a direction counts) or ordinals; the street type may be in any case.
_STREET = re.compile(
    r"\b\d{1,5}\s+(?:(?:[A-Z][A-Za-z']*|\d{1,3}(?:st|nd|rd|th))\.?\s+){1,3}"
    r"(?i:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Way|Place|Pl)\b\.?"
)
_IPV4 = re.compile(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?![\w]|\.\d)")
_MAC = re.compile(r"(?<![\w:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![\w:-])")

# HL7 v2 segments that carry patient data. The segment ends at a carriage
# return, a newline, or their escaped two-character forms (\r, \n) as they
# appear inside single-line log messages.
_HL7 = re.compile(r"(?:^|(?<=[\s\"'|=:,(\[{;])|(?<=\\r)|(?<=\\n))(?:PID|NK1|GT1|IN1)\|[^\r\n]*?(?=\\r|\\n|\r|\n|$)")

# label -> category. The value after the label is what gets flagged.
_SEP = r"[_ .-]?"          # "patient_name", "patient name", "patient.name", "patient-name", "patientName"
_LABELS = {
    "person_name": rf"patient{_SEP}(?:first|last|middle|given|family|full)?{_SEP}name|pt{_SEP}name|first{_SEP}name|last{_SEP}name|given{_SEP}name|family{_SEP}name|surname|full{_SEP}name|guarantor{_SEP}name|next{_SEP}of{_SEP}kin",
    "record_identifier": rf"patient{_SEP}mrn|patient{_SEP}id|patient{_SEP}number|mrn|medical{_SEP}record(?:{_SEP}(?:number|no|id))?|account{_SEP}(?:number|no|id)|insurance{_SEP}(?:id|number)|member{_SEP}id|policy{_SEP}(?:id|number)|visit{_SEP}(?:id|number)|encounter{_SEP}(?:id|number)",
    "date_of_birth": rf"patient{_SEP}dob|patient{_SEP}birth{_SEP}date|dob|date{_SEP}of{_SEP}birth|birth{_SEP}date|birthday",
    "street_address": rf"home{_SEP}address|street{_SEP}address|mailing{_SEP}address|patient{_SEP}address",
    "ssn": rf"patient{_SEP}ssn|ssn|social{_SEP}security(?:{_SEP}(?:number|no))?",
    "phone": rf"patient{_SEP}phone|home{_SEP}phone|mobile{_SEP}phone|phone{_SEP}number|phone|tel(?:ephone)?",
    "email": rf"patient{_SEP}email|e-?mail",
}
_LABEL_NAMES = [(cat, f"lbl_{i}") for i, cat in enumerate(_LABELS)]
_LABELED = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    + "|".join(f"(?P<{g}>{_LABELS[c]})" for c, g in _LABEL_NAMES)
    # After the label: a closing quote (possibly JSON-escaped, \\"), "#" as in "MRN#:", then = : or =>.
    + r")(?![A-Za-z0-9])(?:\\?[\"']|#)*\s*(?:=>|:|=)\s*"
    # The value: quoted (possibly JSON-escaped) or bare. A bare value starts as one run of non-space text;
    # _extend() widens it for the kinds that span spaces (names, dates, addresses).
    r"(?:\\?\"(?P<dq>[^\"\\]{1,80})\\?\"|'(?P<sq>[^']{1,80})'|(?P<bare>[^\s,;|\"}\])]+))",
    re.IGNORECASE,
)
# "DOB 01/02/1980": a date of birth labelled with a space instead of = or :.
_DOB_SPACED = re.compile(
    r"(?<![A-Za-z0-9])(?:dob|date[_ ]of[_ ]birth|birth[_ ]?date)[ ]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})(?![\w])",
    re.IGNORECASE,
)
# Cheap pre-filters. A label rule needs one of these words; a plain `in` test on lower-cased text is
# many times faster than running the label regex over every message.
_LABEL_KEYS = ("name", "patient", "mrn", "record", "account", "insurance", "member", "policy", "visit",
               "encounter", "dob", "birth", "ssn", "social", "phone", "mail", "address", "kin")


def _may_have_label(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _LABEL_KEYS)


# The SSN, phone and card rules each need a run of digits in one of a few shapes. One search for any
# of those shapes decides whether to run all three; most log lines have none.
_NUMBER_SHAPES = re.compile(
    r"\d{3}-\d{2}-\d{4}|\(\d{3}\)|\d{3}[ .-]\d{3}[ .-]\d{4}|\d{4,6}[ -]\d{4,6}[ -]\d{3,6}|(?<!\d)[2-6]\d{14,18}"
).search
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


def _card_issuer_ok(d: str) -> bool:
    """Digits start like a real card network and have its length. Luhn alone
    passes about 1 in 10 random numbers, which is far too many in logs full of counters."""
    n = len(d)
    if d[0] == "4":
        return n in (16, 19)                                    # Visa
    if d[:2] in ("34", "37"):
        return n == 15                                          # American Express
    if 51 <= int(d[:2]) <= 55 or 2221 <= int(d[:4]) <= 2720:
        return n == 16                                          # Mastercard
    if d[:4] == "6011" or d[:2] == "65" or 644 <= int(d[:3]) <= 649:
        return n in (16, 19)                                    # Discover
    if 3528 <= int(d[:4]) <= 3589:
        return n in (16, 19)                                    # JCB
    return False


def _hits_card(text: str) -> list[_Hit]:
    out = []
    for m in _CARD.finditer(text):
        # The match is greedy and can swallow a number that follows the card ("4111 1111 1111 1111 123").
        # Try the whole run first, then drop trailing groups until one is a real card number.
        # It can just as well start with a short number before the card ("12:00:01 4111 ...", "qty 2 4111 ...").
        groups = [(g.group(), g.start()) for g in re.finditer(r"\d+", m.group())]
        found = False
        for first in range(len(groups)):
            for last in range(len(groups), first, -1):
                digits = "".join(g for g, _ in groups[first:last])
                if len(digits) < 15 or not (_card_issuer_ok(digits) and _luhn_ok(digits)):
                    continue
                start = m.start() + groups[first][1]
                end = m.start() + groups[last - 1][1] + len(groups[last - 1][0])
                if end < len(text) and text[end] == "." and text[end + 1:end + 2].isdigit():
                    continue                                    # the start of a decimal number
                out.append(_Hit("credit_card", MEDIUM, start, end))
                found = True
                break
            if found:
                break
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


_NAME_STOP = frozenset({"a", "an", "and", "at", "by", "for", "from", "has", "had", "in", "is", "of", "on", "or",
                        "the", "to", "was", "were", "with", "not", "no"})
_NAME_NEXT = re.compile(r"[ ]*,?[ ]+([^\W\d_](?:[^\W\d_]|['\u2019.-])*)(?![=:\w])")
_NAME_PARTICLES = frozenset({"van", "von", "de", "del", "della", "der", "den", "di", "da", "du", "la", "le", "bin", "ibn",
                             "al", "el", "st", "mc", "mac", "ter", "ten", "af"})
_SSN_VALUE = re.compile(r"\d{3}[ -]\d{2}[ -]\d{4}")
_PHONE_VALUE = re.compile(r"(?:\+?1[ .-])?(?:\(\d{3}\)[ ]?|\d{3}[ .-])\d{3}[ .-]\d{4}")
_DOB_VALUES = (
    re.compile(r"[A-Za-z]{3,9}\.?[ ]\d{1,2}(?:st|nd|rd|th)?,?[ ]\d{4}"),     # January 5, 1980
    re.compile(r"\d{1,2}[ ][A-Za-z]{3,9}\.?,?[ ]\d{4}"),                      # 5 January 1980
    re.compile(r"\d{1,2}[ ]\d{1,2}[ ]\d{2,4}|\d{4}[ ]\d{1,2}[ ]\d{1,2}"),       # 01 02 1980
)
_ID_VALUE = re.compile(r"\d[\w-]*(?:[ ]\d[\w-]*){0,3}")                        # 1234 5678
_ADDRESS_VALUE = re.compile(r"[^;|\"\r\n]{1,80}")


def _extend(category: str, text: str, start: int, end: int) -> int:
    """End of a bare labelled value. The first run of non-space text is the least it can be; kinds that
    normally contain spaces take the rest: a name's other words, a date, a phone or SSN, an address."""
    end = start + len(text[start:end].rstrip(",.;:"))
    if category == "person_name":
        allow_lower = text[start:start + 1].islower()           # "john smith" is all lower case; "John Smith was" is not
        words = 0
        for _ in range(5):
            m = _NAME_NEXT.match(text, end)
            if m is None:
                break
            word = m.group(1)
            low = word.lower().strip(".'-")
            particle = low in _NAME_PARTICLES                    # "van Beethoven", "de la Cruz": part of the name
            if low in _NAME_STOP or not (particle or allow_lower or not word[0].islower()):
                break
            if not particle:
                words += 1
                if words > 2:
                    break
            end = m.end()
        return start + len(text[start:end].rstrip(",.;:"))
    if category == "street_address":
        m = _ADDRESS_VALUE.match(text, start)
        return start + len(m.group().rstrip(" ,.:")) if m is not None else end
    if category == "ssn":
        m = _SSN_VALUE.match(text, start)
    elif category == "phone":
        m = _PHONE_VALUE.match(text, start)
    elif category == "date_of_birth":
        m = next((r.match(text, start) for r in _DOB_VALUES if r.match(text, start)), None)
    elif category == "record_identifier":
        m = _ID_VALUE.match(text, start)
    else:
        m = None
    return m.end() if m is not None else end


def _hits_labeled(text: str) -> list[_Hit]:
    out = []
    for m in _LABELED.finditer(text):
        category = next((c for c, g in _LABEL_NAMES if m.group(g)), None)
        for group in ("dq", "sq", "bare"):
            if m.group(group) is not None:
                start, end = m.start(group), m.end(group)
                break
        else:
            continue
        # Trim spaces at both ends (a quoted value can start or end with one) so the range is exactly the value.
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end <= start or text[start:end].lower() in _PLACEHOLDERS:
            continue
        if group == "bare":
            end = _extend(category, text, start, end)
            if end <= start or text[start:end].lower() in _PLACEHOLDERS:
                continue
        if category == "phone" and sum(c.isdigit() for c in text[start:end]) < 7:
            continue
        out.append(_Hit(category, HIGH, start, end))
    return out


def _hits_dob_spaced(text: str) -> list[_Hit]:
    return [_Hit("date_of_birth", HIGH, m.start(1), m.end(1)) for m in _DOB_SPACED.finditer(text)]


# (cheap test that must be true for the rule to possibly match, rule)
_RULES: list[tuple[Callable[[str], bool], Callable[[str], list[_Hit]]]] = [
    (lambda t: "|" in t, _hits_hl7),
    (lambda t: (":" in t or "=" in t) and _may_have_label(t), _hits_labeled),
    (lambda t: _may_have_label(t), _hits_dob_spaced),
    (lambda t: "@" in t, _hits_email),
    (lambda t: " " in t, _hits_street),
    (lambda t: "." in t, _hits_ip),
    (lambda t: ":" in t or "-" in t, _hits_mac),
]


def _resolve_overlaps(hits: list[_Hit]) -> list[_Hit]:
    """Two rules can claim overlapping text (a labelled street address is also a street address, and the
    label rule may stop earlier than the full-shape rule). Overlapping spans of the same group (personal or
    network) are merged into one that covers all of them, so masking never leaves a piece behind and never
    works on overlapping ranges. The merged span takes the kind of its most confident, longest member."""
    merged: list[_Hit] = []
    for network in (False, True):
        part = sorted((h for h in hits if (h.category in NETWORK_CATEGORIES) == network), key=lambda h: (h.start, h.end))
        cur: Optional[_Hit] = None
        for h in part:
            if cur is not None and h.start < cur.end:
                best = max((cur, h), key=lambda x: (_RANK[x.confidence], x.end - x.start))
                top = max((cur, h), key=lambda x: _RANK[x.confidence]).confidence
                cur = _Hit(best.category, top, min(cur.start, h.start), max(cur.end, h.end))
            else:
                if cur is not None:
                    merged.append(cur)
                cur = h
        if cur is not None:
            merged.append(cur)
    return sorted(merged, key=lambda h: (h.start, h.end))


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
    if _NUMBER_SHAPES(text) is not None:
        hits.extend(_hits_ssn(text))
        hits.extend(_hits_phone(text))
        hits.extend(_hits_card(text))
    return tuple(Flag(field, h.category, h.confidence, h.start, h.end) for h in _resolve_overlaps(hits))


def scan_event_fields(*, message: Optional[str], device_id: Optional[str] = None,
                      process: Optional[str] = None, category: Optional[str] = None) -> list[Flag]:
    """Flags across every scanned field of one event."""
    flags: list[Flag] = list(scan_text(message, "message"))
    for name, value in (("device_id", device_id), ("process", process), ("category", category)):
        flags.extend(scan_text(value, name))
    return flags


def split_flags(flags: list[Flag]) -> tuple[list[Flag], list[Flag]]:
    """(personal, network) flags."""
    personal = [f for f in flags if category_group(f.category) == "personal"]
    return personal, [f for f in flags if category_group(f.category) == "network"]


def scan_columns(*, message: Optional[str], device_id: Optional[str] = None,
                 process: Optional[str] = None, category: Optional[str] = None) -> dict:
    """What to store on an event row after scanning it: the scanner version and the two flag lists."""
    personal, network = split_flags(scan_event_fields(
        message=message, device_id=device_id, process=process, category=category))
    return {
        "privacy_scan_version": SCANNER_VERSION,
        "sensitive_flags": encode_flags(personal),
        "network_flags": encode_flags(network),
    }


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
