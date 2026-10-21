"""
Local import: log files already on this machine. Covers a thumb drive, a
hospital-supplied capture, a folder someone downloaded from /portrait/diags
by hand, or files uploaded through the UI (the upload route saves them to a
folder and points this source at it). Works fully offline.

Also replaces the upload-only idea in collectors/hub_import.py: a hub log a
field engineer pulled manually comes in through here.

remote_id is the file's sha256, so importing the same file twice is skipped
even if it was renamed or copied somewhere else.

Local imports are not time-filtered: modified times on copied media are not
reliable, so every file chosen is imported in full and the since/until range
is ignored. Filtering by log timestamp would belong in the ingestion
pipeline (follow-up).
"""
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.sources.base import ALLOWED_SUFFIXES, LogSource, RemoteFile, SourceError, SourceStatus

MAX_DEPTH = 5  # how deep to walk a folder; a thumb drive is not a filesystem crawl


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk(root: Path, depth: int = 0):
    for entry in sorted(root.iterdir()):
        if entry.is_symlink() or entry.name.startswith("."):
            continue
        if entry.is_dir():
            if depth < MAX_DEPTH:
                yield from _walk(entry, depth + 1)
        elif entry.is_file():
            yield entry


class LocalImportSource(LogSource):
    id = "local"
    display_name = "Local files / ZIP import"
    needs_network = False

    def is_configured(self) -> SourceStatus:
        return SourceStatus(ok=True)

    def list_available(self, since: datetime, until: datetime, options: dict[str, str]) -> list[RemoteFile]:
        raw = (options or {}).get("path")
        if not raw:
            raise SourceError("Choose a file or folder to import.")
        root = Path(raw).expanduser()
        if not root.exists():
            raise SourceError(f"{root} does not exist. Check the path or that the drive is plugged in.")

        candidates = [root] if root.is_file() else list(_walk(root))
        files = []
        for path in candidates:
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            digest = sha256_of(path)
            stat = path.stat()
            files.append(
                RemoteFile(
                    source=self.id,
                    remote_id=digest,
                    name=path.name,
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    checksum=digest,
                    scope=str(path),  # where it came from, so fetch() can find it
                )
            )
        if not files and root.is_file():
            raise SourceError(
                f"{root.name} is not a supported log file ({', '.join(sorted(ALLOWED_SUFFIXES))})."
            )
        return files

    def fetch(self, remote: RemoteFile, dest_dir: Path) -> Path:
        src = Path(remote.scope or "")
        if not src.is_file():
            raise SourceError(f"{remote.name} is no longer at {src}.")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / remote.name
        tmp = dest.with_name(dest.name + ".part")
        try:
            shutil.copyfile(src, tmp)
            tmp.replace(dest)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise SourceError(f"Could not copy {remote.name}: {e.strerror or e}") from e
        return dest
