"""
Database side of PHI/PII flagging: the per-collection report and rescanning.

The report answers the user story's guarantee directly: "no known-pattern
sensitive data makes it into the output unflagged". `all_scanned` is true only
when every event in the collection was judged by the CURRENT scanner version,
so a package built from a collection can say, with evidence, that nothing was
left unchecked. It reports counts only; it never contains matched text.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import Session

from app.models import NormalizedEvent as E
from app.privacy.scanner import SCANNER_VERSION, decode_flags, scan_columns
from app.storage.filters import UNASSIGNED

RESCAN_BATCH = 2000


def _in_collection(job_id: str):
    return E.job_id.is_(None) if job_id == UNASSIGNED else E.job_id == job_id


def _needs_scan():
    return or_(E.privacy_scan_version.is_(None), E.privacy_scan_version < SCANNER_VERSION)


@dataclass
class FileFlagCount:
    source_file: Optional[str]
    events: int
    flagged_events: int
    unscanned_events: int
    network_events: int = 0


@dataclass
class PrivacyReport:
    job_id: str
    scanner_version: int
    events: int
    scanned_events: int
    unscanned_events: int          # never scanned, or scanned by an older scanner
    flagged_events: int            # events with personal-data flags; IP and MAC addresses do not count
    network_events: int = 0        # events with IP or MAC addresses (recorded apart from the personal kinds)
    network_by_category: dict[str, int] = field(default_factory=dict)
    flags_by_category: dict[str, int] = field(default_factory=dict)
    flags_by_confidence: dict[str, int] = field(default_factory=dict)
    flags_by_field: dict[str, int] = field(default_factory=dict)
    files: list[FileFlagCount] = field(default_factory=list)

    @property
    def all_scanned(self) -> bool:
        return self.unscanned_events == 0


def privacy_report(db: Session, job_id: str) -> PrivacyReport:
    scope = _in_collection(job_id)
    total = db.scalar(select(func.count()).select_from(E).where(scope)) or 0
    unscanned = db.scalar(select(func.count()).select_from(E).where(scope, _needs_scan())) or 0
    flagged = db.scalar(select(func.count()).select_from(E).where(scope, E.sensitive_flags.is_not(None))) or 0

    network = db.scalar(select(func.count()).select_from(E).where(scope, E.network_flags.is_not(None))) or 0
    net_cat: Counter = Counter()
    for (raw,) in db.execute(select(E.network_flags).where(scope, E.network_flags.is_not(None))).yield_per(5000):
        for f in decode_flags(raw):
            net_cat[f.category] += 1

    by_cat: Counter = Counter()
    by_conf: Counter = Counter()
    by_field: Counter = Counter()
    # Only flagged events are read, and only their flag column: the log text itself is never loaded.
    for (raw,) in db.execute(select(E.sensitive_flags).where(scope, E.sensitive_flags.is_not(None))).yield_per(5000):
        flags = decode_flags(raw)
        if not flags:                       # damaged value: still counts as flagged, kind unknown
            by_cat["unreadable"] += 1
            continue
        for f in flags:
            by_cat[f.category] += 1
            by_conf[f.confidence] += 1
            by_field[f.field] += 1

    rows = db.execute(
        select(
            E.source_file,
            func.count(),
            func.sum(case((E.sensitive_flags.is_not(None), 1), else_=0)),
            func.sum(case((_needs_scan(), 1), else_=0)),
            func.sum(case((E.network_flags.is_not(None), 1), else_=0)),
        ).where(scope).group_by(E.source_file).order_by(E.source_file)
    ).all()
    files = [FileFlagCount(name, n, int(fl or 0), int(un or 0), int(nw or 0)) for name, n, fl, un, nw in rows]

    return PrivacyReport(
        job_id=job_id, scanner_version=SCANNER_VERSION, events=total,
        scanned_events=total - unscanned, unscanned_events=unscanned, flagged_events=flagged,
        network_events=network, network_by_category=dict(net_cat),
        flags_by_category=dict(by_cat), flags_by_confidence=dict(by_conf),
        flags_by_field=dict(by_field), files=files,
    )


def rescan(db: Session, job_id: Optional[str] = None) -> int:
    """Scan every event the current scanner has not judged yet: events from
    before this feature existed, or ones scanned by an older rule set. With
    job_id, only that collection (or UNASSIGNED). Returns how many events were
    scanned. Safe to repeat; each batch is committed on its own."""
    conds = [_needs_scan()]
    if job_id is not None:
        conds.append(_in_collection(job_id))
    done = 0
    while True:
        rows = db.execute(
            select(E.id, E.message, E.device_id, E.process, E.category).where(and_(*conds)).limit(RESCAN_BATCH)
        ).all()
        if not rows:
            return done
        db.execute(update(E), [
            {"id": r.id, **scan_columns(message=r.message, device_id=r.device_id,
                                         process=r.process, category=r.category)}
            for r in rows
        ])
        db.commit()
        done += len(rows)
