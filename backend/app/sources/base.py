"""
The log source plugin interface (SCRUM-43, "Acquire Logs").

A log source is anywhere raw log files come from: a local file/folder/ZIP
(thumb drives, hospital-supplied captures), the Portrait Mobile platform's
own diags export, or Box. Every source implements this one contract, so the
acquisition service, the manifest and the API never special-case a source.

This is deliberately separate from collectors/: a collector deploys and runs
a tool against a device (run(target_ip) / cleanup()). A source just hands
over files that already exist, or that it asks the platform to export.

Boundary with ingestion: a source produces raw files plus metadata. Turning
those into NormalizedEvent rows is ingestion's job (pipeline.ingest_file).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# File types worth acquiring. ZIPs are unpacked by the acquisition service.
ALLOWED_SUFFIXES = {".csv", ".zip", ".log", ".txt"}


class SourceError(Exception):
    """A failure with a message safe to show the field engineer: it names
    what went wrong and what to do, and never contains a secret value."""


@dataclass(frozen=True)
class RemoteFile:
    """One file a source can hand over. For listing sources (local, Box) it
    already exists; for export sources (Portrait diags) it describes the
    export fetch() will request."""
    source: str
    remote_id: str                       # stable id at the source
    name: str
    size: int | None = None
    modified_at: datetime | None = None
    checksum: str | None = None          # whatever the source reports (Box sha1, local sha256)
    scope: str | None = None             # where it came from: original path for local
                                         # imports, service/device for exports
    window_start: datetime | None = None
    window_end: datetime | None = None


@dataclass
class SourceStatus:
    ok: bool
    reason: str = ""


@dataclass
class SourceOptions:
    """Choices a source offers the UI, e.g. which service/device to export
    from Portrait diags. Empty for sources that take no options."""
    fields: dict[str, list[str]] = field(default_factory=dict)


class LogSource(ABC):
    id: str = "unnamed"
    display_name: str = "Unnamed source"
    needs_network: bool = False      # False means it works offline
    # How far back to look when the caller gives no start time. None means
    # the global limit (settings.acquire_max_days). Sources where each window
    # is an API call set something smaller.
    default_lookback: timedelta | None = None

    @abstractmethod
    def is_configured(self) -> SourceStatus:
        """Cheap, no network. ok=False with a reason (e.g. 'BOX_FOLDER_ID is
        not set') means the source is shown as unavailable, not as broken."""

    def test_connection(self) -> SourceStatus:
        """Reach the source and report a specific reason on failure
        (unreachable, credentials rejected, folder not shared...).
        Sources with nothing to reach just repeat is_configured()."""
        return self.is_configured()

    def options(self) -> SourceOptions:
        return SourceOptions()

    @abstractmethod
    def list_available(
        self, since: datetime, until: datetime, options: dict[str, str]
    ) -> list[RemoteFile]:
        """Files (or planned exports) in the time range. Filter at the source
        where possible so we never download everything and filter after.
        Raise SourceError for anything the user should see."""

    @abstractmethod
    def fetch(self, remote: RemoteFile, dest_dir: Path) -> Path:
        """Write the file into dest_dir and return its path. Raise SourceError
        on failure; never leave a partial file behind under the final name."""
