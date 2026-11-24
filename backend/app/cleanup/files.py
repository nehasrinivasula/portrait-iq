"""Pure helpers for the cleanup choice: item ids and the job folders on disk.
No database or Kubernetes here, so they are easy to test."""
from __future__ import annotations

import os
import re
import shutil
import time
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


def _dir_size(path: Path) -> int:
    """Every byte under a run folder, the export zip included (unlike
    folder_stats, which skips the protected export): retention has to account
    for the whole folder because the package zip holds the big artifacts too."""
    total = 0
    for root, _dirs, names in os.walk(path, followlinks=False):
        for n in names:
            fp = Path(root) / n
            try:
                if not fp.is_symlink():
                    total += fp.stat().st_size
            except OSError:
                continue
    return total


def job_dirs_newest_first() -> list[Path]:
    """Every run's folder under the job output dir, newest first by mtime."""
    base = Path(settings.job_output_dir)
    if not base.is_dir():
        return []
    dirs = [p for p in base.iterdir() if p.is_dir() and not p.is_symlink()]
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def prune_old_runs(keep: int, max_total_mb: int, protect: frozenset[str] = frozenset(),
                   min_age_seconds: int = 0) -> list[str]:
    """Bound the data volume. Always keep the newest `keep` run folders; among
    the rest, delete oldest-first until the total size of all run folders is
    under max_total_mb. Returns the ids removed. A full run folder -- export zip
    and all -- is deleted, so old exports age out; this is what keeps the
    multi-GB TSDB export (SCRUM-105) from filling the PVC. Disabled when keep and
    the cap are both 0.

    Two guards keep retention from deleting output something still needs, so a
    run started while another is in flight cannot prune the other's folder:
      * `protect` -- ids never removed. The caller passes EVERY queued/running
        run (not just the one starting), so concurrent runs can't delete each
        other's output.
      * `min_age_seconds` -- a run folder younger than this is never pruned,
        whatever the cap. Package builds and downloads happen after a run
        finishes; this leaves a window in which a just-finished run's export is
        safe from retention. (Download availability is derived from the file on
        disk, so a run pruned later shows as expired, never as a dead link.)"""
    if keep <= 0 and max_total_mb <= 0:
        return []
    now = time.time()
    sized = [(p, _dir_size(p)) for p in job_dirs_newest_first()]   # newest first
    newest = {p.name for p, _ in sized[:keep]} if keep > 0 else set()
    total = sum(s for _, s in sized)
    cap = max_total_mb * 1024 * 1024
    removed: list[str] = []
    for path, size in reversed(sized):                            # oldest first
        if cap and total <= cap:
            break
        if path.name in newest or path.name in protect:
            continue
        if min_age_seconds > 0:
            try:
                if now - path.stat().st_mtime < min_age_seconds:
                    continue
            except OSError:
                continue
        try:
            shutil.rmtree(path)
        except OSError:
            continue
        removed.append(path.name)
        total -= size
    return removed


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
