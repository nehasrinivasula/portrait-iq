"""
Runs every rule in rules.RULES over a job's normalized events and writes
one Signal row per rule that matched at least once. Deliberately dumb and
single-pass — optimize only once you know this is actually slow on real
data volumes, not before.
"""
import json

from sqlalchemy.orm import Session

from app.analysis.rules import RULES
from app.knowledge.lookup import lookup_signal
from app.models import NormalizedEvent, Signal


def run_analysis(db: Session, job_id: str) -> list[Signal]:
    events = db.query(NormalizedEvent).filter(NormalizedEvent.job_id == job_id).all()

    signals: list[Signal] = []
    for rule in RULES:
        matches = [e for e in events if rule.match(e)]
        if not matches:
            continue

        signal = Signal(
            job_id=job_id,
            rule_id=rule.id,
            title=rule.title_template.format(n=len(matches)),
            severity=rule.severity,
            count=len(matches),
            status="unconfirmed",
            actions_json=json.dumps(rule.draft_actions),
        )

        # Ask the knowledge layer if it has a real manual match. Today this
        # always comes back unconfirmed — see knowledge/lookup.py — but the
        # call site is here so wiring in real manuals later is a one-file change.
        match = lookup_signal(rule.id)
        signal.status = match.status
        signal.manual_ref = match.manual_ref
        if match.actions:
            signal.actions_json = json.dumps(match.actions)

        db.add(signal)
        signals.append(signal)

    db.commit()
    return signals
