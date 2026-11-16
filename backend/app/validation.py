"""Shared input validators for free-text fields (requirement D-9).

Pure functions with no framework imports, so routes, pydantic models and
tests all use the same rules. The frontend mirrors them in
frontend/src/validation.ts; keep the limits in step.
"""
from __future__ import annotations

import ipaddress
import re
import unicodedata

# Limits shared with the UI.
MAX_USERNAME = 128
MAX_PASSWORD = 256
MAX_FILTER_VALUE = 200       # log "equals" values and message search
MAX_ID = 128                 # audit user and session id filters
MAX_LABEL = 64               # device labels
MAX_HOST = 253
MAX_UPLOAD_NAME = 255

_HOSTNAME_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


class ValidationFailure(ValueError):
    """The message is safe to show to the person who typed the value."""


def has_control_chars(value: str) -> bool:
    """True for control characters, including newline and tab (Unicode category Cc),
    plus invisible format characters such as zero-width spaces and bidi overrides (Cf)."""
    return any(unicodedata.category(ch) in ("Cc", "Cf") for ch in value)


def clean_text(value: str | None, label: str, *, max_length: int, required: bool = False) -> str | None:
    """Trim, then check length and characters. Returns None for an empty optional value."""
    text = (value or "").strip()
    if not text:
        if required:
            raise ValidationFailure(f"Enter {label}.")
        return None
    if len(text) > max_length:
        raise ValidationFailure(f"{label[0].upper()}{label[1:]} is limited to {max_length} characters.")
    if has_control_chars(text):
        raise ValidationFailure(f"{label[0].upper()}{label[1:]} can't contain control or invisible characters.")
    return text


def validate_ipv4(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        raise ValidationFailure("Enter the IP address of the Portrait Mobile server.")
    try:
        # ipaddress rejects leading zeros ("010.0.0.1") since they are ambiguous (octal in some tools).
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        raise ValidationFailure("Enter a valid IPv4 address, for example 192.168.0.3.") from None


def validate_host(value: str | None) -> str:
    """An IPv4 address or a DNS host name (letters, digits, hyphens, dots)."""
    text = (value or "").strip()
    if not text:
        raise ValidationFailure("Enter a host name or IP address.")
    if len(text) > MAX_HOST:
        raise ValidationFailure(f"Host name is limited to {MAX_HOST} characters.")
    if re.fullmatch(r"[0-9.]+", text):
        return validate_ipv4(text)
    labels = text.rstrip(".").split(".")
    if not all(_HOSTNAME_LABEL.match(part) for part in labels):
        raise ValidationFailure("Use letters, digits and hyphens only, with dots between parts (for example lab-pc.local).")
    return text


def validate_port(value: int | str | None) -> int:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationFailure("Enter a port number between 1 and 65535.") from None
    if not 1 <= port <= 65535:
        raise ValidationFailure("Enter a port number between 1 and 65535.")
    return port


def validate_label(value: str | None) -> str | None:
    """Free-text device label: optional, up to MAX_LABEL characters, no control characters."""
    return clean_text(value, "device label", max_length=MAX_LABEL)


def validate_upload_name(name: str | None, allowed_suffixes: set[str] | frozenset[str]) -> str:
    """Reject empty names, path separators, control characters and unsupported file types."""
    text = (name or "").strip()
    if not text:
        raise ValidationFailure("The file has no name.")
    if len(text) > MAX_UPLOAD_NAME:
        raise ValidationFailure(f"File names are limited to {MAX_UPLOAD_NAME} characters.")
    if has_control_chars(text) or "/" in text or "\\" in text:
        raise ValidationFailure("File names can't contain slashes or control characters.")
    dot = text.rfind(".")
    suffix = text[dot:].lower() if dot > 0 else ""
    if suffix not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ValidationFailure(f"'{text}' isn't a supported file type. Use one of: {allowed}.")
    return text
