"""
Phase 2. Andrew Welch's ask in the EU interview: "how many times was Hub A
used... how many patients did they test last week" — a usage question,
not a fault question. That's why this reads from normalized_events
directly rather than from Signal rows: analysis answers "what broke",
this answers "how was it used", and they need different source data.

Deliberately not wired into any route yet. Wire it up once the event
store has real, multi-day, multi-device data to make "usage" meaningful —
computing this over 35 hours from one test rig won't tell you anything.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import NormalizedEvent


def device_activity_count(db: Session, device_id: str, start: datetime, end: datetime) -> int:
    """Rough placeholder for 'how often was this hub used' — counts events
    in range as a proxy for activity. Replace with a real definition of
    'usage' once you know what signal in the logs actually means
    'a patient was being monitored' (this needs input from GE engineering,
    not a guess from event volume alone)."""
    return (
        db.query(NormalizedEvent)
        .filter(
            NormalizedEvent.device_id == device_id,
            NormalizedEvent.timestamp >= start,
            NormalizedEvent.timestamp <= end,
        )
        .count()
    )
