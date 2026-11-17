"""Pure helpers for the cleanup choice: item ids and the job folders on disk.
No database or Kubernetes here, so they are easy to test."""
from __future__ import annotations

import os
import re
from pathlib import Path

from app.config import settings

ITEM_ID = re.compile(r"^(pod|files):([A-Za-z0-9][A-Za-z0-9._-]{0,127})$")
MAX_ITEMS = 200


class CleanupError(ValueError):
    """A request the engineer can fix; the message is safe to show."""


def folder_stats(path: Path) -> tuple[int, int]:
    count = size = 0
    for root, _dirs, names in os.walk(path, followlinks=False):
        for n in names:
            fp = Path(root) / n
            try:
                if not fp.is_symlink():
                    size += fp.stat().st_size
                    count += 1
            except OSError:
                continue
    return count, size


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
