"""Message helpers shared by the coverage report and the findings reader (no database)."""
import re

from app.privacy.scanner import decode_flags, mask_text, scan_columns

EXAMPLE_CHARS = 300

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_HEX = re.compile(r"\b(?:0x)?[0-9a-f]{6,}\b", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")


def shape(message: str) -> str:
    """The message with what varies folded away, so repeats of one event group together."""
    text = _UUID.sub("<id>", message)
    text = _HEX.sub(lambda m: "<hex>" if re.search(r"[a-f]", m.group(), re.IGNORECASE) and re.search(r"\d", m.group()) else m.group(), text)
    return _SPACES.sub(" ", _DIGITS.sub("#", text)).strip()[:EXAMPLE_CHARS]


def masked(message: str) -> str:
    """The message with personal PHI/PII replaced, judged now by the current scanner."""
    flags = decode_flags(scan_columns(message=message)["sensitive_flags"])
    return mask_text(message, flags, "message") or ""
