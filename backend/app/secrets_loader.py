"""Secrets handling for the IoMT Analysis Tool (SCRUM-72).

Four small pieces, all in one module so they are easy to review:

1. load_secret()           - read a secret from an env var or from a file
                             (the same code works for a local secrets file
                             and for a Kubernetes Secret mounted as files).
2. Secret                  - wrapper whose str()/repr()/format() never show the value.
3. install_log_redaction() - masks known secret values (and common token patterns)
                             in every log message and traceback.
4. assert_no_secrets()     - scans files / directories / zips before export and
                             raises if a known secret value (or a private key) is found.

Rules this module is built around:
  * Secret values are never written to logs, error messages, or exported files.
  * Error messages name the secret (e.g. BOX_CLIENT_SECRET), never its value.
  * Secrets come from the environment or a file outside the repo, never from code.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import threading
import traceback
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator

__all__ = [
    "Secret",
    "SecretError",
    "MissingSecretError",
    "AmbiguousSecretError",
    "InsecureSecretFileError",
    "ExportGuardError",
    "Finding",
    "load_secret",
    "register_for_redaction",
    "redaction_scope",
    "clear_registered_secrets",
    "redact",
    "install_log_redaction",
    "scan_for_secrets",
    "assert_no_secrets",
]

MASK = "***"

# Values shorter than this are not registered for redaction. Masking a 1-3
# character string would corrupt ordinary log text. Real credentials are far longer.
MIN_REDACT_LENGTH = 6

# Numeric group ID allowed to have read access to secret files: the pod's
# fsGroup on Kubernetes. Unset means secret files must be owner-only.
TRUSTED_GID_VAR = "SECRETS_FILE_GID"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class SecretError(Exception):
    """Base class. Messages never contain secret values."""


class MissingSecretError(SecretError):
    """A required secret was not provided."""


class AmbiguousSecretError(SecretError):
    """Both NAME and NAME_FILE are set, so it is unclear which one to use."""


class InsecureSecretFileError(SecretError):
    """A secrets file is readable or writable by users it should not be."""


class ExportGuardError(SecretError):
    """A secret (or private key) was found in data that is about to be exported."""


# --------------------------------------------------------------------------- #
# Registry of secret values (used for log redaction and the export scan)
# --------------------------------------------------------------------------- #

_lock = threading.Lock()
_registered: dict[str, str] = {}  # secret value -> secret name, kept for the process lifetime
_scoped: dict[str, list] = {}  # secret value -> [secret name, number of open scopes]


def register_for_redaction(value: str, name: str = "runtime") -> None:
    """Remember a secret value so it is masked in logs and caught by the export scan.

    For application secrets that live as long as the process. load_secret()
    does this automatically. Call this yourself for secrets you only learn at
    runtime, e.g. the access token Box returns after you authenticate - those
    never pass through load_secret(). For a credential that only matters
    during one request, use redaction_scope() instead.
    """
    if value is None or len(value) < MIN_REDACT_LENGTH:
        return
    with _lock:
        _registered[value] = name


@contextmanager
def redaction_scope(value: str, name: str = "runtime") -> Iterator[None]:
    """Mask value in logs and the export scan only while the block runs.

    For request-scoped credentials, e.g. a password typed into the Connect
    screen: it is masked while the request is handled, then forgotten, so
    the registry does not keep every password ever entered.
    Scopes may nest or overlap (two requests with the same password).
    """
    if value is None or len(value) < MIN_REDACT_LENGTH:
        yield
        return
    with _lock:
        entry = _scoped.setdefault(value, [name, 0])
        entry[1] += 1
    try:
        yield
    finally:
        with _lock:
            entry = _scoped.get(value)
            if entry is not None:  # absent only if clear_registered_secrets() ran meanwhile
                entry[1] -= 1
                if entry[1] <= 0:
                    del _scoped[value]


def clear_registered_secrets() -> None:
    """Forget all registered values (mainly for tests)."""
    with _lock:
        _registered.clear()
        _scoped.clear()


def _snapshot() -> dict[str, str]:
    with _lock:
        merged = {value: entry[0] for value, entry in _scoped.items()}
        merged.update(_registered)
        return merged


# --------------------------------------------------------------------------- #
# Secret wrapper
# --------------------------------------------------------------------------- #

class Secret:
    """A secret value that does not reveal itself when printed or logged.

    Use .reveal() only at the exact point where the value is sent to the
    service that needs it.
    """

    __slots__ = ("_name", "_value")

    def __init__(self, name: str, value: str) -> None:
        self._name = name
        self._value = value

    @property
    def name(self) -> str:
        return self._name

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Secret({self._name}={MASK})"

    __str__ = __repr__

    def __reduce__(self):  # refuse pickling so a secret is not copied to disk by accident
        raise TypeError("Secret objects cannot be pickled")


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def _trusted_gid() -> int | None:
    raw = os.environ.get(TRUSTED_GID_VAR, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise SecretError(f"{TRUSTED_GID_VAR} must be a numeric group ID.") from None


def _check_file_permissions(path: Path, name: str) -> None:
    """Reject secret files that anyone but their owner could read or change.

    Allowed: 0400 and 0600, owned by this process's user or by root. Group
    read (0440, 0640) is allowed only when the file's group is the one named
    in SECRETS_FILE_GID: that is what Kubernetes produces for a Secret volume
    when the pod sets fsGroup. On a shared host any other group could
    include other people.
    Skipped on Windows, where POSIX mode bits are not meaningful.
    """
    if os.name == "nt":
        return
    st = path.stat()  # follows symlinks, which Kubernetes mounts use
    mode = st.st_mode
    if st.st_uid not in (os.geteuid(), 0):
        raise InsecureSecretFileError(
            f"File for secret {name} is owned by another user (uid {st.st_uid}). "
            f"Use a file you own."
        )
    if mode & stat.S_IRWXO or mode & stat.S_IWGRP or mode & stat.S_IXGRP:
        raise InsecureSecretFileError(
            f"File for secret {name} has unsafe permissions ({stat.filemode(mode)}). "
            f"Run: chmod 600 <file>  (or set defaultMode: 0400 on the Kubernetes volume)."
        )
    if mode & stat.S_IRGRP and st.st_gid != _trusted_gid():
        raise InsecureSecretFileError(
            f"File for secret {name} is readable by its group (gid {st.st_gid}) "
            f"({stat.filemode(mode)}). Run: chmod 600 <file>. On Kubernetes with "
            f"fsGroup, set {TRUSTED_GID_VAR} to the fsGroup."
        )


def load_secret(name: str, *, required: bool = True) -> Secret | None:
    """Load secret NAME.

    Looks for, in this order:
      NAME        - the value itself, in an environment variable
      NAME_FILE   - environment variable holding the path of a file with the value

    Setting both is an error (it is ambiguous). A trailing newline in a file is removed.
    If the secret is missing: raises MissingSecretError when required, else returns None.
    """
    file_var = f"{name}_FILE"
    env_value = os.environ.get(name)
    file_path = os.environ.get(file_var)

    if env_value and file_path:
        raise AmbiguousSecretError(
            f"Both {name} and {file_var} are set. Set only one."
        )

    if env_value:
        value = env_value
    elif file_path:
        path = Path(file_path)
        if not path.is_file():
            raise MissingSecretError(
                f"{file_var} points to a file that does not exist or is not a file."
            )
        _check_file_permissions(path, name)
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
        if not value:
            raise MissingSecretError(f"The file for secret {name} is empty.")
    else:
        if required:
            raise MissingSecretError(
                f"Secret {name} is not set. Set the environment variable {name}, "
                f"or set {file_var} to the path of a file containing it."
            )
        return None

    register_for_redaction(value, name)
    return Secret(name, value)


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #

# Patterns that look like credentials even when we never registered the value.
_BEARER = re.compile(r"(?i)(\bauthorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+/=\-]+")
_KEY_VALUE = re.compile(
    r"(?i)(\b(?:access_token|refresh_token|client_secret|api_key|apikey|password|passwd|secret)"
    r"\b['\"]?\s*[=:]\s*)(['\"]?)[^\s'\",&}]+"
)
_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)


def redact(text: str) -> str:
    """Return text with registered secret values and common token patterns masked."""
    if not text:
        return text
    # Longest values first so a secret that contains another is fully masked.
    for value in sorted(_snapshot(), key=len, reverse=True):
        text = text.replace(value, MASK)
    text = _PEM_BLOCK.sub(MASK, text)
    text = _BEARER.sub(lambda m: m.group(1) + MASK, text)
    text = _KEY_VALUE.sub(lambda m: m.group(1) + m.group(2) + MASK, text)
    return text


def _format_exc(exc_info) -> str:
    return "".join(traceback.format_exception(*exc_info)).rstrip("\n")


# Attributes every LogRecord has. Anything else on a record came from extra=.
_STANDARD_RECORD_FIELDS = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None))
) | {"message", "asctime"}


def _redact_extra_fields(record: logging.LogRecord) -> None:
    """Mask secrets in fields added with extra=, which a formatter or a
    structured (e.g. JSON) handler may output. Strings are always redacted;
    other values are replaced by their redacted text only when it differs,
    so a number used with %(n)d keeps working."""
    for key, value in list(vars(record).items()):
        if key in _STANDARD_RECORD_FIELDS:
            continue
        if isinstance(value, str):
            setattr(record, key, redact(value))
            continue
        try:
            text = str(value)
        except Exception:
            setattr(record, key, "<log field suppressed: could not be formatted safely>")
            continue
        masked = redact(text)
        if masked != text:
            setattr(record, key, masked)


def install_log_redaction() -> None:
    """Mask secrets in every log record created from now on.

    Works at record-creation time, so it covers every logger and every handler,
    including ones added later. Messages, %-style arguments, tracebacks, stack
    info and fields passed with extra= are all redacted. Safe to call more
    than once.
    """
    _install_extra_redaction()
    current = logging.getLogRecordFactory()
    if getattr(current, "_redacting", False):
        return

    def factory(*args, **kwargs):
        record = current(*args, **kwargs)
        try:
            record.msg = redact(record.getMessage())
        except Exception:
            # logging would otherwise print the raw msg/args while reporting its own error
            record.msg = "<log message suppressed: could not be formatted safely>"
        record.args = None
        if record.exc_info and record.exc_info[0] is not None:
            record.exc_text = redact(_format_exc(record.exc_info))
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return record

    factory._redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def _install_extra_redaction() -> None:
    """extra= fields are copied onto the record by Logger.makeRecord after the
    record factory has returned, so the factory never sees them. Wrap
    makeRecord on the Logger class, which also covers loggers created before
    this call."""
    original = logging.Logger.makeRecord
    if getattr(original, "_redacting", False):
        return

    def make_record(self, *args, **kwargs):
        record = original(self, *args, **kwargs)
        _redact_extra_fields(record)
        return record

    make_record._redacting = True  # type: ignore[attr-defined]
    logging.Logger.makeRecord = make_record  # type: ignore[method-assign]


# --------------------------------------------------------------------------- #
# Export guard
# --------------------------------------------------------------------------- #

_CHUNK = 1024 * 1024  # 1 MiB
_PRIVATE_KEY_MARKER = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER_BYTES = re.compile(rb"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~+/=\-]{20,}")


@dataclass(frozen=True)
class Finding:
    """One problem found by the scan. Never contains the secret value itself."""

    location: str  # file path, or "archive.zip::member"
    what: str  # name of a registered secret, or a description of the pattern

    def __str__(self) -> str:
        return f"{self.location}: {self.what}"


def _scan_stream(
    stream: BinaryIO,
    location: str,
    needles: dict[bytes, str],
    chunk_size: int,
) -> list[Finding]:
    found: dict[str, None] = {}  # ordered, de-duplicated
    maxlen = max((len(n) for n in needles), default=0)
    overlap = max(maxlen, 64) - 1  # also enough to catch markers split across chunks
    tail = b""
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        window = tail + chunk
        for needle, name in needles.items():
            if needle in window:
                found[f"registered secret {name}"] = None
        if _PRIVATE_KEY_MARKER.search(window):
            found["private key"] = None
        if _BEARER_BYTES.search(window):
            found["bearer token"] = None
        tail = window[-overlap:] if overlap > 0 else b""
    return [Finding(location, what) for what in found]


def scan_for_secrets(paths: Iterable[str | os.PathLike], *, chunk_size: int = _CHUNK) -> list[Finding]:
    """Scan files, directories (recursively) and .zip archives for secrets.

    Looks for every registered secret value, private key blocks, and bearer tokens.
    Returns a list of findings; an empty list means nothing was found.
    """
    needles = {v.encode("utf-8"): n for v, n in _snapshot().items()}
    findings: list[Finding] = []

    def scan_file(path: Path) -> None:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    with zf.open(info) as member:
                        findings.extend(
                            _scan_stream(member, f"{path}::{info.filename}", needles, chunk_size)
                        )
            return
        with open(path, "rb") as fh:
            findings.extend(_scan_stream(fh, str(path), needles, chunk_size))

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file():
                    scan_file(child)
        elif p.is_file():
            scan_file(p)
        else:
            raise FileNotFoundError(f"Cannot scan {p}: not found")
    return findings


def assert_no_secrets(paths: Iterable[str | os.PathLike], *, chunk_size: int = _CHUNK) -> None:
    """Raise ExportGuardError if anything secret-looking is found. Call before creating the zip."""
    findings = scan_for_secrets(paths, chunk_size=chunk_size)
    if findings:
        lines = "\n".join(f"  - {f}" for f in findings)
        raise ExportGuardError(
            f"Export blocked: {len(findings)} possible secret(s) found.\n{lines}"
        )
