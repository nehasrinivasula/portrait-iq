"""
Files a field engineer hands to Portrait IQ instead of letting it collect
them (SCRUM-39): a capture hospital IT already made, for tshark's import
mode; a hub log pulled by hand (hub_import); and RTI GUI exports the operator
saves from the RTI tools for the UMF RTI import mode (SCRUM-123) -- an Admin
Console discovery data export (rti_admin) or a Monitor saved session (rti_monitor).

Each upload is stored as <upload_dir>/<id>.<kind>, where id is a random
token: nothing from the client's file name reaches a path, a log line or
the audit log (a file name can name a patient). The content must start
like its kind, and the size limit is checked while the file is written.
Files are owner-only, since a capture can contain PHI/PII.
"""
import os
import re
import secrets
from pathlib import Path
from typing import BinaryIO

from app.config import settings

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
CHUNK_BYTES = 1024 * 1024

# First bytes of each accepted format (compared against the file's first 4
# bytes, so each prefix is at most 4 bytes long).
KINDS: dict[str, tuple[bytes, ...]] = {
    "pcap": (
        b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4",   # pcap, microsecond timestamps
        b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d",   # pcap, nanosecond timestamps
        b"\x0a\x0d\x0d\x0a",                        # pcapng
    ),
    # RTI Admin Console discovery data export (File > Export Discovery Data):
    # XML. Admin Console 6.1.2 starts it with a "<!-- This is a discovery data
    # file ..." comment and no XML declaration (bench export, 2026-11-24); a
    # declaration or a UTF-8 BOM is accepted too (SCRUM-123, the UMF RTI GUI route).
    "rti_admin": (b"<!--", b"<?xm", b"\xef\xbb\xbf"),
    # RTI Monitor saved session: a Java-serialized object stream
    # (STREAM_MAGIC 0xACED + version 0x0005). Only RTI Monitor can read it.
    "rti_monitor": (b"\xac\xed\x00\x05",),
}


class UploadError(Exception):
    """A rejected upload, with a message safe to show the user."""


def is_valid_id(value) -> bool:
    return isinstance(value, str) and bool(ID_PATTERN.match(value))


def path_for(upload_id: str, kind: str) -> Path:
    if not is_valid_id(upload_id) or kind not in KINDS:
        raise UploadError("unknown upload")
    return Path(settings.upload_dir) / f"{upload_id}.{kind}"


def exists(upload_id: str, kind: str) -> bool:
    try:
        return path_for(upload_id, kind).is_file()
    except UploadError:
        return False


def save(stream: BinaryIO, kind: str) -> tuple[str, int]:
    """Store the stream as a new upload; returns (upload_id, size)."""
    if kind not in KINDS:
        raise UploadError(f"unsupported upload kind {kind!r}; supported: {sorted(KINDS)}")
    limit = settings.upload_max_mb * 1024 * 1024
    upload_id = secrets.token_hex(16)
    dest = path_for(upload_id, kind)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    size = 0
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            while chunk := stream.read(CHUNK_BYTES):
                if size == 0 and not chunk[:4].startswith(KINDS[kind]):
                    raise UploadError(f"this file is not a {kind} file")
                size += len(chunk)
                if size > limit:
                    raise UploadError(f"the file is larger than the {settings.upload_max_mb} MB limit")
                f.write(chunk)
        if size == 0:
            raise UploadError("the file is empty")
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return upload_id, size
