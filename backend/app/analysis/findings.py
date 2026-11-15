"""Reading findings back out: a collection's signals with their evidence, ready for the API."""
import json
from datetime import timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.analysis.text import masked
from app.models import NormalizedEvent as E
from app.models import Signal, SignalEvidence
from app.schemas import DeviceCountOut, EvidenceEventOut, SignalOut


def _utc(ts):
    return ts.replace(tzinfo=timezone.utc) if ts is not None else None       # stored as naive UTC


def _masked_or_none(value: Optional[str]) -> Optional[str]:
    return masked(value) if value else value


def findings_for(db: Session, job_id: str) -> list[SignalOut]:
    signals = db.query(Signal).filter(Signal.job_id == job_id).order_by(Signal.rule_id).all()
    if not signals:
        return []
    links = (db.query(SignalEvidence).filter(SignalEvidence.signal_id.in_([s.id for s in signals]))
             .order_by(SignalEvidence.signal_id, SignalEvidence.rank).all())
    events = {e.id: e for e in db.query(E).filter(E.id.in_({l.event_id for l in links})).all()} if links else {}
    out = []
    for s in signals:
        try:
            devices = json.loads(s.devices_json) if s.devices_json else {}
        except ValueError:
            devices = {}
        evidence = []
        for link in (l for l in links if l.signal_id == s.id):
            e = events.get(link.event_id)
            if e is None:            # removed from the event store since (e.g. a duplicate): skip, never invent
                continue
            evidence.append(EvidenceEventOut(
                event_id=e.id, timestamp=_utc(e.timestamp), device_id=_masked_or_none(e.device_id),
                severity=e.severity, source_file=e.source_file, source_line=e.source_line,
                message=masked(e.message or ""),
            ))
        out.append(SignalOut(
            id=s.id, rule_id=s.rule_id, title=s.title, severity=s.severity, count=s.count, status=s.status,
            manual_ref=s.manual_ref, actions=json.loads(s.actions_json or "[]"),
            first_seen=_utc(s.first_seen), last_seen=_utc(s.last_seen),
            devices=[DeviceCountOut(device_id=d or None, count=n) for d, n in devices.get("top", [])],
            devices_total=int(devices.get("total", 0) or 0),
            evidence=evidence,
        ))
    return out
