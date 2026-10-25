"""
Decides whether a collection's logs are all there, and says exactly what is
missing when they are not. Pure Python: the database layer gathers the
numbers, this module judges them.

"Complete" means every row of every file we read became a stored event, every
stored event is still in the store, nothing is waiting to be ingested, and the
collection has finished. Anything else is reported as a problem with the file
and the count, never silently ignored.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

PROBLEM = "problem"
INFO = "info"

RUNNING_STATUSES = ("queued", "running")
FAILED_STATUSES = ("failed", "cleanup_failed")
# Manifest statuses meaning the file's events are NOT in the view.
NOT_STORED = {
    "failed": "could not be ingested",
    "unrecognized": "is in a format no parser recognises yet",
    "over_budget": "was skipped because the run reached its size limit",
    "acquired": "was downloaded but not ingested",
}


@dataclass
class FileLedger:
    """What ingest recorded for one stored file, next to what is in the store now."""
    source_file: str
    parser: Optional[str]
    rows_read: int
    events_expected: int       # events written at ingest time
    rows_rejected: int
    events_in_store: int       # events currently in the store for this file
    events_removed: int = 0    # deliberately deleted since (see storage.removal)

    @property
    def events_now_expected(self) -> int:
        return self.events_expected - self.events_removed


@dataclass
class ManifestEntry:
    name: str
    status: str
    error: Optional[str] = None


@dataclass
class Issue:
    kind: str
    severity: str              # PROBLEM | INFO
    message: str
    file: Optional[str] = None


def assess(
    *,
    job_status: Optional[str],
    files: list[FileLedger],
    manifest: list[ManifestEntry],
    events_without_record: int,
) -> list[Issue]:
    issues: list[Issue] = []

    if job_status in RUNNING_STATUSES:
        issues.append(Issue("collection_running", PROBLEM,
                            "The collection has not finished, so more logs may still arrive."))
    elif job_status in FAILED_STATUSES:
        issues.append(Issue("collection_failed", PROBLEM,
                            f"The collection ended with status '{job_status}'; its logs may be partial."))

    if not files and events_without_record == 0:
        issues.append(Issue("no_logs", PROBLEM, "No logs were stored for this collection."))

    for f in files:
        if f.events_in_store != f.events_now_expected:
            issues.append(Issue(
                "store_mismatch", PROBLEM,
                f"{f.events_now_expected} events should be in the store but {f.events_in_store} are.",
                f.source_file))
        if f.events_removed:
            issues.append(Issue(
                "events_removed", INFO,
                f"{f.events_removed} events were removed on purpose after ingest (duplicates).",
                f.source_file))
        if f.rows_rejected:
            issues.append(Issue(
                "rows_rejected", PROBLEM,
                f"{f.rows_rejected} of {f.rows_read} rows could not be converted and are not in the view.",
                f.source_file))
        if f.rows_read != f.events_expected + f.rows_rejected:
            issues.append(Issue(
                "unaccounted_rows", PROBLEM,
                f"{f.rows_read} rows were read but only {f.events_expected} stored and "
                f"{f.rows_rejected} rejected.",
                f.source_file))

    if events_without_record:
        issues.append(Issue(
            "untracked_events", PROBLEM,
            f"{events_without_record} events have no ingest record (stored before tracking existed), "
            "so their completeness cannot be confirmed."))

    for m in manifest:
        if m.status in NOT_STORED:
            reason = f" ({m.error})" if m.error else ""
            issues.append(Issue("file_not_stored", PROBLEM,
                                f"This file {NOT_STORED[m.status]}{reason}.", m.name))
        elif m.status == "duplicate":
            issues.append(Issue("duplicate_file", INFO,
                                "Same content as a file already stored; skipped to avoid double counting.", m.name))
    return issues


def is_complete(issues: list[Issue]) -> bool:
    return not any(i.severity == PROBLEM for i in issues)
