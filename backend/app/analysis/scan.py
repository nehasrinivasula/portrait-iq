"""
The matching half of the analysis engine, with no database: events in, per-rule evidence out.
engine.py feeds it from the event store; tests feed it lists.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from app.analysis.rules import RULES

EVIDENCE_EACH_END = 5        # earliest and latest this many matching events are kept as evidence


@dataclass
class EventRow:
    id: int
    timestamp: datetime
    device_id: Optional[str]
    severity: str
    message: str


@dataclass
class _Acc:
    """What one rule has seen so far."""
    count: int = 0
    devices: Counter = field(default_factory=Counter)
    first: list = field(default_factory=list)       # (timestamp, id) of the earliest few
    last: list = field(default_factory=list)        # ... and the latest few

    def add(self, row: EventRow) -> None:
        self.count += 1
        self.devices[row.device_id or ""] += 1
        key = (row.timestamp, row.id)
        self.first.append(key)
        self.last.append(key)
        # Trim now and then rather than on every event.
        if len(self.first) > 4 * EVIDENCE_EACH_END:
            self.first = sorted(self.first)[:EVIDENCE_EACH_END]
            self.last = sorted(self.last)[-EVIDENCE_EACH_END:]

    def evidence(self) -> list:
        first = sorted(self.first)[:EVIDENCE_EACH_END]
        last = sorted(self.last)[-EVIDENCE_EACH_END:]
        return sorted(set(first) | set(last))        # time order, no event twice


def scan(rows: Iterable[EventRow], rules=RULES) -> dict:
    """rule id -> _Acc for every rule that matched at least once. No database."""
    accs = {r.id: _Acc() for r in rules}
    for row in rows:
        for rule in rules:
            if rule.matches(row.severity, row.message):
                accs[rule.id].add(row)
    return {rid: a for rid, a in accs.items() if a.count}
