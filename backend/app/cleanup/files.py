"""Pure helpers for the cleanup choice: item ids and the job folders on disk.
No database or Kubernetes here, so they are easy to test."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from app.config import settings

ITEM_ID = re.compile(r"^(pod|files):([A-Za-z0-9][A-Za-z0-9._-]{0,127})$")
MAX_ITEMS = 200


class CleanupError(ValueError):
    """A request the engineer can fix; the message is safe to show."""


# A run's export (<job>/package/*.zip) is something the engineer asked for and can still
# download, even for an interrupted run. It is never listed or removed as a leftover.
PROTECTED_DIRS = frozenset({"package"})


def folder_stats(path: Path) -> tuple[int, int]:
    """Files and bytes in a job folder, not counting the protected export folder."""
    count = size = 0
    for root, dirs, names in os.walk(path, followlinks=False):
        if Path(root) == path:
            dirs[:] = [d for d in dirs if d not in PROTECTED_DIRS]
        for n in names:
            fp = Path(root) / n
            try:
                if not fp.is_symlink():
                    size += fp.stat().st_size
                    count += 1
            except OSError:
                continue
    return count, size


def clear_job_files(folder: Path) -> None:
    """Delete what a run collected, but keep the export folder and the folder itself."""
    for entry in folder.iterdir():
        if entry.name in PROTECTED_DIRS and entry.is_dir() and not entry.is_symlink():
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def safe_job_dir(job_id: str) -> Path | None:
    """The job's folder, only if it really lies inside the job output folder."""
    base = Path(settings.job_output_dir).resolve()
    path = (base / job_id).resolve()
    return path if path.parent == base and path.is_dir() else None


def parse_ids(ids: list[str]) -> list[str]:
    if not ids:
        raise CleanupError("Choose at least one item.")
    if len(ids) > MAX_ITEMS:
        raise CleanupError(f"Choose at most {MAX_ITEMS} items at a time.")
    for i in ids:
        if not ITEM_ID.match(i):
            raise CleanupError("One of the items is not valid.")
    return list(dict.fromkeys(ids))
