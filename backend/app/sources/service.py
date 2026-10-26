"""
Acquisition service: pull new files from any LogSource into staging, record
them in the manifest, and optionally hand them to ingestion.

    list_available -> drop already-acquired -> size budget -> fetch
      -> unpack ZIPs safely -> ingest (optional) -> manifest + audit

Rules it enforces, from the requirements doc and team objectives:
  - Only new files: anything in the manifest as a success is skipped, so a
    second run over the same range pulls nothing (SCRUM-43 story).
  - Time range within the last N days (Objective 1: 30 days).
  - Preserve partial results: one bad file is recorded as failed and the run
    carries on. Failed files are retried next run.
  - File-size limits: a run never stages more than max_bytes.
  - Audit every run. Never put file contents or secrets in the audit log.
"""
import logging
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.audit import log_action
from app.config import settings
from app.ingestion.parsers.base import UnrecognizedLogFormat
from app.ingestion.pipeline import IngestError, ingest_file
from app.models import AcquiredFileRecord
from app.models import NormalizedEvent as EventRow
from app.secrets_loader import redact
from app.sources.base import ALLOWED_SUFFIXES, ONE_MS, LogSource, RemoteFile, SourceError, window_file
from app.sources.devices import harvest_csv, record_devices
from app.storage.removal import remove_events
from app.sources.local_import import sha256_of

log = logging.getLogger(__name__)

# Counted as "new" in a run's result.
SUCCESS_STATUSES = ("acquired", "ingested", "unrecognized")
# Fully handled: never pulled again. "unrecognized" is NOT here on purpose, so
# a file pulled before its parser existed is retried once one is added.
DONE_STATUSES = ("acquired", "ingested")
# Skipped on the next run. "duplicate" = same bytes as a file we already have
# under another id; remembered so it is not downloaded again every run.
SKIP_STATUSES = DONE_STATUSES + ("duplicate",)
CLOCK_SKEW = timedelta(minutes=5)  # tolerate a laptop clock slightly behind the platform
OVER_BUDGET_MSG = "Skipped: this run reached its size limit. Narrow the time range or raise the limit."


class WindowError(ValueError):
    """The requested time range is invalid; message is shown to the user."""


class _OverBudget(SourceError):
    pass


@dataclass
class FileOutcome:
    name: str
    remote_id: str
    status: str            # acquired | ingested | unrecognized | failed | already_acquired | duplicate | over_budget
    size: int | None = None
    events: int | None = None
    error: str | None = None
    local_path: str | None = None


@dataclass
class AcquisitionResult:
    run_id: str
    source: str
    since: datetime
    until: datetime
    files: list[FileOutcome] = field(default_factory=list)
    bytes_staged: int = 0
    notes: list[str] = field(default_factory=list)   # e.g. end moved back for late logs

    def count(self, *statuses: str) -> int:
        return sum(1 for f in self.files if f.status in statuses)

    @property
    def new_files(self) -> int:
        return self.count(*SUCCESS_STATUSES)


# --- time window --------------------------------------------------------------

def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def resolve_window(since: datetime | None, until: datetime | None, now: datetime | None = None,
                   max_days: int | None = None,
                   default_lookback: timedelta | None = None) -> tuple[datetime, datetime]:
    """Defaults to the full allowed window, or to default_lookback when the
    source sets one. Naive datetimes are taken as UTC."""
    now = _utc(now or datetime.now(timezone.utc))
    max_days = max_days or settings.acquire_max_days
    earliest = now - timedelta(days=max_days)
    if since:
        since = _utc(since)
    else:
        since = max(earliest, now - default_lookback) if default_lookback else earliest
    until = _utc(until) if until else now
    if since >= until:
        raise WindowError("The start of the time range must be before the end.")
    if since < earliest - CLOCK_SKEW:
        raise WindowError(f"Logs can only be pulled from the last {max_days} days.")
    if until > now + CLOCK_SKEW:
        raise WindowError("The end of the time range is in the future.")
    return since, until


# --- manifest -----------------------------------------------------------------

def _existing(db: Session, remote: RemoteFile) -> AcquiredFileRecord | None:
    q = db.query(AcquiredFileRecord).filter(
        AcquiredFileRecord.source == remote.source,
        AcquiredFileRecord.remote_id == remote.remote_id,
    )
    q = q.filter(AcquiredFileRecord.remote_checksum == remote.checksum) if remote.checksum \
        else q.filter(AcquiredFileRecord.remote_checksum.is_(None))
    return q.order_by(AcquiredFileRecord.id.desc()).first()


def is_already_acquired(db: Session, remote: RemoteFile) -> bool:
    rec = _existing(db, remote)
    return rec is not None and rec.status in SKIP_STATUSES


# --- time windows (SCRUM-44) ------------------------------------------------------

def clamp_to_source(source: LogSource, since: datetime, until: datetime) -> tuple[datetime, list[str]]:
    """Apply the source's hold-back for recent data. Returns the end to use and
    a note for the user when it moved. Raises WindowError if nothing is left."""
    end = source.effective_until(until)
    if end >= until:
        return until, []
    reason = source.hold_back_reason() or "recent data is held back by this source"
    if end <= since:
        raise WindowError(f"Nothing to pull yet: {reason}.")
    return end, [f"End moved back to {_utc(end):%Y-%m-%d %H:%M} UTC: {reason}."]


def _covered(db: Session, remote: RemoteFile, start: datetime, end: datetime,
             scopes: list[str | None]) -> list[tuple[datetime, datetime]]:
    """Half-open intervals of [start, end) already pulled successfully by this
    source for this scope or a broader one (an all-devices pull covers every
    device)."""
    q = db.query(AcquiredFileRecord.window_start, AcquiredFileRecord.window_end).filter(
        AcquiredFileRecord.source == remote.source,
        AcquiredFileRecord.scope.in_(scopes),
        AcquiredFileRecord.status.in_(DONE_STATUSES),
        AcquiredFileRecord.window_start.is_not(None),
        AcquiredFileRecord.window_end.is_not(None),
        AcquiredFileRecord.window_start < end,
        AcquiredFileRecord.window_end >= start,
    )
    return sorted((_utc(ws), _utc(we) + ONE_MS) for ws, we in q.all())


def _gaps(start: datetime, end: datetime, covered: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    gaps, cursor = [], start
    for c_start, c_end in covered:
        if c_end <= cursor:
            continue
        if c_start >= end:
            break
        if c_start > cursor:
            gaps.append((cursor, c_start))
        cursor = max(cursor, c_end)
        if cursor >= end:
            break
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def plan_new_windows(db: Session, remotes: list[RemoteFile],
                     source: LogSource | None = None) -> list[tuple[RemoteFile, bool]]:
    """Decide what to request. Returns (remote, already_covered) in order.

    Time-window remotes (the log export API) are compared by TIME, not by id:
    a request for 09:30-10:30 after 09:00-10:00 was pulled becomes just
    10:00-10:30, so overlapping pulls never fetch the same minutes twice.
    Older hour-window manifest rows count as covered too. Failed windows are
    not "done", so they are requested again. Remotes without a window (local
    files, Box) pass through; acquire() checks them by id and checksum."""
    planned: list[tuple[RemoteFile, bool]] = []
    for r in remotes:
        if r.window_start is None or r.window_end is None:
            planned.append((r, False))
            continue
        start, end = _utc(r.window_start), _utc(r.window_end) + ONE_MS
        scopes = source.covering_scopes(r.scope) if source else [r.scope]
        gaps = _gaps(start, end, _covered(db, r, start, end, scopes))
        if not gaps:
            planned.append((r, True))
        elif gaps == [(start, end)]:
            planned.append((r, False))
        else:
            planned += [(window_file(r.source, r.scope or "", s, e), False) for s, e in gaps]
    return planned


def _content_seen(db: Session, source: str, sha256: str, exclude_remote_id: str) -> bool:
    """Same bytes already acquired under another id (e.g. a renamed copy)."""
    return db.query(AcquiredFileRecord.id).filter(
        AcquiredFileRecord.source == source,
        AcquiredFileRecord.sha256 == sha256,
        AcquiredFileRecord.remote_id != exclude_remote_id,
        AcquiredFileRecord.status.in_(DONE_STATUSES),
    ).first() is not None


def _record(db: Session, remote: RemoteFile, outcome: FileOutcome, sha256: str | None,
            job_id: str | None) -> None:
    rec = _existing(db, remote) or AcquiredFileRecord(
        source=remote.source, remote_id=remote.remote_id, remote_checksum=remote.checksum,
    )
    rec.name = remote.name
    rec.size = outcome.size if outcome.size is not None else remote.size
    rec.remote_modified_at = remote.modified_at
    rec.scope = remote.scope
    rec.window_start = remote.window_start
    rec.window_end = remote.window_end
    rec.sha256 = sha256
    rec.local_path = outcome.local_path
    rec.status = outcome.status
    rec.events = outcome.events
    rec.error = outcome.error
    rec.job_id = job_id
    rec.acquired_at = datetime.utcnow()
    db.add(rec)
    db.commit()


# --- ZIP handling -------------------------------------------------------------

ZIP_LOG_SUFFIXES = ALLOWED_SUFFIXES - {".zip"}  # no nested ZIPs

def safe_extract(zip_path: Path, dest: Path, max_bytes: int, max_members: int | None = None) -> list[Path]:
    """Extract a ZIP without trusting it: refuse entries that would land
    outside dest, symlinks, too many entries, or more than max_bytes once
    uncompressed (checked while writing, not from the header, which can lie).
    Only log files (.csv/.log/.txt) are written; anything else in the archive
    is skipped, so stray documents or binaries never land in staging."""
    max_members = max_members or settings.zip_max_members
    dest = dest.resolve()
    written: list[Path] = []
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > max_members:
                raise SourceError(f"{zip_path.name} has {len(infos)} files; the limit is {max_members}.")
            for info in infos:
                if Path(info.filename).suffix.lower() not in ZIP_LOG_SUFFIXES:
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise SourceError(f"{zip_path.name} contains a symbolic link, which is not allowed.")
                target = (dest / info.filename).resolve()
                if not target.is_relative_to(dest):
                    raise SourceError(f"{zip_path.name} contains an unsafe path ({info.filename}).")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as out:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        total += len(chunk)
                        if total > max_bytes:
                            raise SourceError(f"{zip_path.name} is too large once unpacked.")
                        out.write(chunk)
                written.append(target)
    except zipfile.BadZipFile as e:
        raise SourceError(f"{zip_path.name} is not a valid ZIP file.") from e
    except SourceError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return written


# --- the run ------------------------------------------------------------------

# Files the ingestion registry may have a parser for: Portrait / log-export
# CSVs and Kubernetes pod logs. Detection decides; others are "unrecognized".
INGEST_SUFFIXES = {".csv", ".log", ".txt"}


def _ingest(paths: list[Path], db: Session, job_id: str | None,
            remote: RemoteFile, root: Path) -> tuple[str, int, str | None]:
    """Ingest every log file. Returns (status, events, note).

    Uses the common-log-format pipeline contract (SCRUM-46):
      - source_label is unique per acquired file, so two different files
        with the same name (two hubs, ZIP members in different folders)
        never replace each other's events, while re-ingesting the SAME file
        after a retry replaces its rows instead of duplicating them.
      - allow_empty: an export window with no logs is a header-only CSV and
        is a normal result, not an error.
      - UnrecognizedLogFormat = no parser yet (retried next run);
        IngestError = parser found but the file is unusable (failed).
    A 0-byte file has no header for any parser to recognise, so it is
    treated here as empty rather than unrecognized."""
    files = [p for p in paths if p.suffix.lower() in INGEST_SUFFIXES]
    if not files:
        return "unrecognized", 0, "No log files found to ingest."
    events, unknown, bad = 0, [], []
    for p in files:
        if p.stat().st_size == 0:
            continue
        rel = p.relative_to(root).as_posix() if p.is_relative_to(root) else p.name
        label = f"{remote.source}/{remote.remote_id}/{rel}"
        try:
            events += ingest_file(p, db, job_id=job_id, source_label=label, allow_empty=True).events_written
        except UnrecognizedLogFormat:
            unknown.append(p.name)
        except IngestError as e:
            bad.append(redact(str(e)))
    if bad:
        return "failed", events, "Could not ingest: " + "; ".join(bad)
    if unknown and len(unknown) == len(files):
        return "unrecognized", 0, "No parser recognizes this log format yet."
    note = f"Not recognized: {', '.join(unknown)}" if unknown else None
    return "ingested", events, note


def _replace_narrower_rows(db: Session, source: LogSource, remote: RemoteFile) -> int:
    """After a broad window (e.g. all devices, 09:00-10:00) is ingested, delete
    the rows that earlier NARROWER pulls (e.g. device X) stored for the same
    time: the broad file contains those lines too. Together with coverage in
    plan_new_windows (broad pulls cover narrow ones), each log line is stored
    once whichever order the pulls happen in. Rows are found through the
    ingestion source_label "<source>/<remote_id>/..." set in _ingest, and
    deleted through storage.removal.remove_events so each file's ingest
    receipt records the removal: the unified view's completeness report then
    shows it as a deliberate removal, not as missing data."""
    if remote.window_start is None or remote.window_end is None:
        return 0
    start, end = _utc(remote.window_start), _utc(remote.window_end)
    candidates = db.query(AcquiredFileRecord).filter(
        AcquiredFileRecord.source == remote.source,
        AcquiredFileRecord.status.in_(DONE_STATUSES),
        AcquiredFileRecord.scope.is_not(None),
        AcquiredFileRecord.scope != remote.scope,
        AcquiredFileRecord.window_start <= end,
        AcquiredFileRecord.window_end >= start,
    ).all()
    ids: list[int] = []
    for rec in candidates:
        if remote.scope not in source.covering_scopes(rec.scope):
            continue   # not narrower (e.g. a different device or category)
        ids += [event_id for (event_id,) in db.query(EventRow.id).filter(
            EventRow.source_file.startswith(f"{rec.source}/{rec.remote_id}/", autoescape=True),
            EventRow.timestamp >= start.replace(tzinfo=None),
            EventRow.timestamp <= end.replace(tzinfo=None),
        )]
    return remove_events(db, ids) if ids else 0


def _learn_devices(db: Session, paths: list[Path], source_id: str) -> None:
    """Add the devices in these files to the device registry (SCRUM-70).
    Best effort: a problem here never fails the acquisition itself."""
    for p in paths:
        if p.suffix.lower() != ".csv":
            continue
        try:
            record_devices(db, harvest_csv(p), learned_from=source_id)
        except Exception:
            db.rollback()
            log.warning("Could not read devices from %s", p.name, exc_info=True)


def _safe_message(e: Exception) -> str:
    if isinstance(e, SourceError):
        return redact(str(e))
    return f"Unexpected error ({type(e).__name__}). See the server log."


def acquire(
    db: Session,
    source: LogSource,
    *,
    actor: str,
    since: datetime | None = None,
    until: datetime | None = None,
    options: dict[str, str] | None = None,
    job_id: str | None = None,
    ingest: bool = False,
    max_bytes: int | None = None,
    staging_root: Path | None = None,
    now: datetime | None = None,
) -> AcquisitionResult:
    """Pull every new file from source in [since, until]. Raises WindowError
    for a bad range and SourceError if the source cannot be listed at all;
    per-file problems are reported in the result instead."""
    since, until = resolve_window(since, until, now=now, default_lookback=source.default_lookback)
    status = source.is_configured()
    if not status.ok:
        raise SourceError(status.reason)
    until, notes = clamp_to_source(source, since, until)
    max_bytes = max_bytes if max_bytes is not None else settings.default_max_output_mb * 1024 * 1024
    run_id = uuid.uuid4().hex[:12]
    run_dir = Path(staging_root or settings.staging_dir) / run_id / source.id
    result = AcquisitionResult(run_id=run_id, source=source.id, since=since, until=until, notes=notes)

    try:
        remotes = source.list_available(since, until, options or {})
    except SourceError as e:
        _audit(db, actor, result, error=str(e))
        raise

    for i, (remote, covered) in enumerate(plan_new_windows(db, remotes, source)):
        if covered or is_already_acquired(db, remote):
            result.files.append(FileOutcome(remote.name, remote.remote_id, "already_acquired", remote.size))
            continue
        remaining = max_bytes - result.bytes_staged
        if remote.size is not None and remote.size > remaining:
            result.files.append(FileOutcome(
                remote.name, remote.remote_id, "over_budget", remote.size,
                error=OVER_BUDGET_MSG,
            ))
            continue

        # Each file gets its own folder so same-named files from different
        # devices or subfolders never overwrite each other.
        file_dir = run_dir / f"{i:04d}"
        outcome = FileOutcome(remote.name, remote.remote_id, "failed", remote.size)
        sha = None
        try:
            # Raw exports may contain PHI/PII. Keep the per-file directory
            # private even if the process umask allows wider access.
            file_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            file_dir.chmod(0o700)
            path = source.fetch(remote, file_dir)
            size = path.stat().st_size
            sha = sha256_of(path)
            outcome.size, outcome.local_path = size, str(path)
            if size > remaining:
                raise _OverBudget(OVER_BUDGET_MSG)
            # Same bytes under another id means a copied FILE (local, Box). For time
            # windows the window is the identity: every quiet window returns the
            # same header-only CSV, and it must still count as pulled.
            if remote.window_start is None and _content_seen(db, source.id, sha, remote.remote_id):
                shutil.rmtree(file_dir, ignore_errors=True)
                outcome.status, outcome.local_path = "duplicate", None
                _record(db, remote, outcome, sha, job_id)
                result.files.append(outcome)
                continue

            staged = [path]
            if path.suffix.lower() == ".zip":
                staged = safe_extract(path, file_dir / path.stem, max_bytes=remaining - size)
                size += sum(p.stat().st_size for p in staged)
            result.bytes_staged += size

            outcome.status = "acquired"
            if ingest:
                outcome.status, outcome.events, outcome.error = _ingest(staged, db, job_id, remote, file_dir)
            # Only files that succeeded feed the device list, never a file we rejected.
            if outcome.status in ("acquired", "ingested"):
                _learn_devices(db, staged, source.id)
            if outcome.status == "ingested":
                replaced = _replace_narrower_rows(db, source, remote)
                if replaced:
                    result.notes.append(
                        f"{remote.name}: replaced {replaced} rows stored earlier by narrower pulls "
                        "(e.g. single devices) for the same time, so each line is stored once."
                    )
        except Exception as e:
            if not isinstance(e, SourceError):
                log.exception("Acquiring %s from %s failed", remote.name, source.id)
            shutil.rmtree(file_dir, ignore_errors=True)
            outcome.status = "over_budget" if isinstance(e, _OverBudget) else "failed"
            outcome.error, outcome.local_path = _safe_message(e), None
        _record(db, remote, outcome, sha, job_id)
        result.files.append(outcome)

    _audit(db, actor, result)
    return result


def _audit(db: Session, actor: str, result: AcquisitionResult, error: str | None = None) -> None:
    detail = (
        f"source={result.source} run={result.run_id} "
        f"since={result.since.isoformat()} until={result.until.isoformat()} "
        f"new={result.new_files} already={result.count('already_acquired', 'duplicate')} "
        f"failed={result.count('failed')} over_budget={result.count('over_budget')} "
        f"bytes={result.bytes_staged}"
    )
    if error:
        detail += f" error={redact(error)}"
    log_action(db, actor, "acquire_logs", detail=detail)
