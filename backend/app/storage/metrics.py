"""
Simple counts over the stored events (A-4, the high-level view of a set of logs):

  * how many events of each severity (DEBUG / INFO / WARN / ERROR) fall in the period,
  * the same counts over time, in minute, hour or day buckets, and
  * a breakdown by service: which process (or category, source, device, component)
    wrote the most errors and warnings.

Plain counts and groupings, no scoring or guessing. Every function does read-only
work on an open Session and honours the same filters as the log search (period,
collection, device, text, and so on), so the numbers always describe exactly the
logs the engineer is looking at.

Severity levels are the four the event store keeps. CRITICAL, FATAL and similar
levels are folded into ERROR when a log is ingested (ingestion/schema.py), so
they are counted as errors here.

Time buckets use SQLite's strftime (the app's database); PostgreSQL's date_trunc
is handled too in case the store is ever moved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.ingestion.schema import SEVERITIES
from app.models import NormalizedEvent as E
from app.storage.events import _conditions
from app.storage.filters import EventFilter, FilterError

# What the breakdown can group by. "source" is a short fixed vocabulary (portrait, kubernetes, ...);
# the others are log fields that can hold flagged text, so a masked view withholds them.
GROUPS = {
    "process": E.process,
    "category": E.category,
    "source": E.source,
    "device": E.device_id,
    "component": E.component,
}
WITHHELD_WHEN_MASKED = frozenset({"process", "category", "device", "component"})

BUCKET_SECONDS = {"minute": 60, "hour": 3600, "day": 86400}
BUCKET_FORMAT = {"minute": "%Y-%m-%dT%H:%M:00", "hour": "%Y-%m-%dT%H:00:00", "day": "%Y-%m-%dT00:00:00"}
MAX_BUCKETS = 500          # a chart with more bars than this is not readable
DEFAULT_TOP = 10
MAX_TOP = 50


@dataclass
class Bucket:
    start: datetime                       # naive UTC, the start of the minute / hour / day
    counts: dict[str, int]                # every severity, zeros included

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass
class GroupRow:
    value: Optional[str]                  # None = events that have no value for the field
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass
class Metrics:
    total: int
    first_timestamp: Optional[datetime]
    last_timestamp: Optional[datetime]
    severities: dict[str, int]
    bucket: Optional[str] = None          # minute | hour | day; None when no series was asked for or there are no events
    buckets: list[Bucket] = field(default_factory=list)
    group_by: str = "process"
    groups: list[GroupRow] = field(default_factory=list)
    groups_total: int = 0                 # how many different values there are, including the ones not listed
    other: Optional[GroupRow] = None      # everything outside the listed groups
    withheld: bool = False                # true: group values were left out because the view is masked


def _zero() -> dict[str, int]:
    return {level: 0 for level in SEVERITIES}


# --- choosing the buckets --------------------------------------------------------------

def _floor(ts: datetime, bucket: str) -> datetime:
    if bucket == "minute":
        return ts.replace(second=0, microsecond=0)
    if bucket == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def _bucket_count(start: datetime, end: datetime, bucket: str) -> int:
    first = _floor(start, bucket)
    seconds = max(0.0, (end - first).total_seconds())
    return int(seconds // BUCKET_SECONDS[bucket]) + 1


def choose_bucket(start: datetime, end: datetime, requested: str = "auto") -> str:
    """The bucket size for a period: the one asked for, or the smallest that keeps the chart
    under MAX_BUCKETS bars. Raises FilterError when no size fits."""
    if requested != "auto":
        if requested not in BUCKET_SECONDS:
            raise FilterError(f"Unknown bucket '{requested}'. Use one of: auto, {', '.join(BUCKET_SECONDS)}.")
        n = _bucket_count(start, end, requested)
        if n > MAX_BUCKETS:
            raise FilterError(f"That period has {n:,} {requested} buckets; the most shown is {MAX_BUCKETS}. "
                              "Choose a larger bucket or a shorter period.")
        return requested
    for name in BUCKET_SECONDS:
        if _bucket_count(start, end, name) <= MAX_BUCKETS:
            return name
    raise FilterError(f"That period is longer than {MAX_BUCKETS} days. Choose a shorter period.")


def _bucket_expression(db: Session, bucket: str):
    if db.get_bind().dialect.name == "postgresql":
        return func.date_trunc(bucket, E.timestamp)
    return func.strftime(BUCKET_FORMAT[bucket], E.timestamp)


def _as_datetime(value) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


# --- the numbers -----------------------------------------------------------------------

def _series(db: Session, conds: list, start: datetime, end: datetime, requested: str) -> tuple[str, list[Bucket]]:
    bucket = choose_bucket(start, end, requested)
    expr = _bucket_expression(db, bucket)
    counts: dict[datetime, dict[str, int]] = {}
    for stamp, level, n in db.execute(
        select(expr.label("b"), E.severity, func.count()).where(*conds).group_by("b", E.severity)
    ):
        counts.setdefault(_as_datetime(stamp), _zero())[level] = n
    # Fill the quiet buckets in: silence on a chart is information, not a gap in the data.
    step = timedelta(seconds=BUCKET_SECONDS[bucket])
    cursor, last = _floor(start, bucket), _floor(end, bucket)
    out = []
    while cursor <= last:
        out.append(Bucket(cursor, counts.get(cursor, _zero())))
        cursor += step
    return bucket, out


def _groups(db: Session, conds: list, group_by: str, top: int, severities: dict[str, int]
            ) -> tuple[list[GroupRow], int, Optional[GroupRow]]:
    column = GROUPS[group_by]
    sums = {level: func.sum(case((E.severity == level, 1), else_=0)).label(level.lower()) for level in SEVERITIES}
    rows = db.execute(
        select(column, *sums.values(), func.count().label("n"))
        .where(*conds).group_by(column)
        # Most errors first, then warnings, then the busiest: what a person scanning for trouble wants on top.
        .order_by(sums["ERROR"].desc(), sums["WARN"].desc(), func.count().desc(), column)
        .limit(top)
    ).all()
    listed = [GroupRow(r[0], {level: int(r[i + 1] or 0) for i, level in enumerate(SEVERITIES)}) for r in rows]
    distinct = db.scalar(select(func.count()).select_from(select(column).where(*conds).group_by(column).subquery())) or 0
    rest = {level: severities[level] - sum(g.counts[level] for g in listed) for level in SEVERITIES}
    other = GroupRow(None, rest) if distinct > len(listed) and any(rest.values()) else None
    return listed, distinct, other


def metrics(db: Session, f: EventFilter, *, group_by: str = "process", bucket: str = "auto",
            top: int = DEFAULT_TOP, series: bool = True, mask: bool = False) -> Metrics:
    """Severity counts, counts over time and a breakdown by `group_by`, for the events matching `f`.

    `series=False` skips the time series (the summary report does not need it). With `mask`,
    the breakdown is left out for fields that can hold flagged text, as the filter options are."""
    if group_by not in GROUPS:
        raise FilterError(f"Unknown group '{group_by}'. Use one of: {', '.join(GROUPS)}.")
    top = max(1, min(top, MAX_TOP))
    conds = _conditions(f)

    severities = _zero()
    first = last = None
    for level, n, lo, hi in db.execute(
        select(E.severity, func.count(), func.min(E.timestamp), func.max(E.timestamp)).where(*conds).group_by(E.severity)
    ):
        severities[level] = severities.get(level, 0) + n
        first = lo if first is None or lo < first else first
        last = hi if last is None or hi > last else last
    total = sum(severities.values())
    result = Metrics(total=total, first_timestamp=first, last_timestamp=last, severities=severities, group_by=group_by)
    if not total:
        return result

    if series:
        # The period asked for, or the span of the events found when a side is left open.
        start = f.since if f.since is not None else first
        end = f.until - timedelta(microseconds=1) if f.until is not None else last
        result.bucket, result.buckets = _series(db, conds, min(start, first), max(end, last), bucket)

    if mask and group_by in WITHHELD_WHEN_MASKED:
        result.withheld = True
    else:
        result.groups, result.groups_total, result.other = _groups(db, conds, group_by, top, severities)
    return result
