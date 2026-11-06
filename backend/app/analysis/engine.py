"""
Runs every rule in rules.RULES over a collection's normalized events and writes one Signal per rule
that matched at least once, with the evidence for it: how many events, when first and last, which
devices, and the earliest and latest matching events (signal_evidence).

One pass over the events in id order, in batches, so a collection of hundreds of thousands of rows is
never held in memory, and every event is offered to every rule: an event may support more than one
finding. Running it again replaces the collection's earlier signals instead of adding duplicates.
"""
import json
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.rules import RULES
from app.analysis.scan import EventRow, scan
from app.knowledge.lookup import lookup_signal
from app.models import NormalizedEvent as E
from app.models import Signal, SignalEvidence

BATCH = 5000
TOP_DEVICES = 10


def iter_events(db: Session, job_id: str, batch: Optional[int] = None) -> Iterable[EventRow]:
    """The collection's events in id order, a batch at a time. Only the columns the rules read."""
    batch = batch or BATCH
    last_id = 0
    while True:
        rows = db.execute(
            select(E.id, E.timestamp, E.device_id, E.severity, E.message)
            .where(E.job_id == job_id, E.id > last_id).order_by(E.id).limit(batch)
        ).all()
        if not rows:
            return
        for r in rows:
            yield EventRow(r.id, r.timestamp, r.device_id, r.severity, r.message or "")
        last_id = rows[-1].id


def run_analysis(db: Session, job_id: str, rules: tuple = RULES) -> list[Signal]:
    found = scan(iter_events(db, job_id), rules)

    # Replace, don't append: running twice must not double every finding.
    old = [s.id for s in db.query(Signal.id).filter(Signal.job_id == job_id).all()]
    if old:
        db.query(SignalEvidence).filter(SignalEvidence.signal_id.in_(old)).delete(synchronize_session=False)
        db.query(Signal).filter(Signal.id.in_(old)).delete(synchronize_session=False)

    signals: list[Signal] = []
    for rule in rules:
        acc = found.get(rule.id)
        if acc is None:
            continue
        evidence = acc.evidence()
        top = acc.devices.most_common(TOP_DEVICES)
        signal = Signal(
            job_id=job_id,
            rule_id=rule.id,
            title=rule.title_template.format(n=acc.count),
            severity=rule.severity,
            count=acc.count,
            status="unconfirmed",
            actions_json=json.dumps(list(rule.draft_actions)),
            first_seen=evidence[0][0] if evidence else None,
            last_seen=evidence[-1][0] if evidence else None,
            devices_json=json.dumps({"total": len(acc.devices), "top": [[d, n] for d, n in top]}),
        )
        # Ask the knowledge layer if it has a real manual match. Today this always comes back
        # unconfirmed - see knowledge/lookup.py - but the call site is here so wiring in real
        # manuals later is a one-file change.
        match = lookup_signal(rule.id)
        signal.status = match.status
        signal.manual_ref = match.manual_ref
        if match.actions:
            signal.actions_json = json.dumps(match.actions)
        db.add(signal)
        db.flush()                                    # gives the signal its id
        db.add_all(SignalEvidence(signal_id=signal.id, event_id=eid, rank=i) for i, (_, eid) in enumerate(evidence))
        signals.append(signal)

    db.commit()
    return signals
