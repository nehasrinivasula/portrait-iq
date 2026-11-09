"""
Request/reply sequences rebuilt from parsed log lines (ingestion/messages.py):
which requests were answered, how fast, which were refused (nak) and which
never got a reply. Read-only, computed at query time from the event store.

Pairing, per component (messages.PAIRING):
  "id"    request and reply share a correlation_id (e.g. uuidRequest).
  "next"  no id is logged (HL7 health check): a request pairs with the next
          reply from the same device and component within PAIR_WINDOW.
Components that never log replies are left out, so they are not reported as
"unanswered" when that is just how they log.

States:
  answered    a reply arrived; status (if any) is not a nak
  nak         a reply arrived and refused the request (status starts "nak")
  unanswered  no reply, and the request is older than PAIR_WINDOW
  pending     no reply yet, but the request is too recent to call it lost
  unexpected  a reply with no matching request in the searched window (the
              request may be just before it, or the requester had given up)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.messages import PAIRING
from app.models import NormalizedEvent as E
from app.storage.events import _conditions
from app.storage.filters import EventFilter, FilterError

PAIR_WINDOW = timedelta(seconds=60)
STATES = ("answered", "nak", "unanswered", "pending", "unexpected")
MAX_EXCHANGE_LINES = 200_000   # keep one call bounded; narrow the time range beyond this
_REPLY_KINDS = ("reply", "response")


@dataclass
class Sequence:
    state: str
    component: Optional[str]
    command: Optional[str]
    device_id: Optional[str]
    correlation_id: Optional[str]
    request_event_id: Optional[int]
    request_at: Optional[datetime]
    reply_event_id: Optional[int]
    reply_at: Optional[datetime]
    status: Optional[str]

    @property
    def clock_skew(self) -> bool:
        """The reply is logged before its request: the two devices' clocks
        disagree (or the pairing guessed wrong), so no latency is given."""
        return self.request_at is not None and self.reply_at is not None and self.reply_at < self.request_at

    @property
    def latency_ms(self) -> Optional[int]:
        if self.request_at is None or self.reply_at is None or self.clock_skew:
            return None
        return int((self.reply_at - self.request_at).total_seconds() * 1000)

    @property
    def started_at(self) -> Optional[datetime]:
        return self.request_at or self.reply_at


def _state_for_reply(status: Optional[str]) -> str:
    return "nak" if status and status.lower().startswith("nak") else "answered"


def _open_request_state(request_at: datetime, now: datetime) -> str:
    return "pending" if now - request_at < PAIR_WINDOW else "unanswered"


def _from_rows(req, rep, state: str) -> Sequence:
    base = req if req is not None else rep
    return Sequence(
        state=state,
        component=base.component,
        command=(req.command if req is not None and req.command else None) or (rep.command if rep is not None else None),
        device_id=base.device_id,
        correlation_id=base.correlation_id,
        request_event_id=req.id if req is not None else None,
        request_at=req.timestamp if req is not None else None,
        reply_event_id=rep.id if rep is not None else None,
        reply_at=rep.timestamp if rep is not None else None,
        status=rep.status if rep is not None else None,
    )


def build_sequences(rows: Iterable, now: datetime) -> list[Sequence]:
    """Pair request and reply rows (ordered by time) into sequences. `now` is
    the end of the searched window, used to tell pending from unanswered."""
    by_id: dict[tuple, list] = defaultdict(list)
    by_next: dict[tuple, list] = defaultdict(list)
    for r in rows:
        mode = PAIRING.get(r.component)
        if mode == "id" and r.correlation_id:
            by_id[(r.component, r.correlation_id)].append(r)
        elif mode == "next":
            by_next[(r.component, r.device_id)].append(r)

    out: list[Sequence] = []
    for group in by_id.values():
        requests = [r for r in group if r.message_kind == "request"]
        replies = [r for r in group if r.message_kind in _REPLY_KINDS]
        for i, req in enumerate(requests):
            rep = replies[i] if i < len(replies) else None
            out.append(_from_rows(req, rep, _state_for_reply(rep.status) if rep is not None
                                  else _open_request_state(req.timestamp, now)))
        for rep in replies[len(requests):]:
            out.append(_from_rows(None, rep, "unexpected"))

    for group in by_next.values():
        open_req = None
        for r in group:
            if r.message_kind == "request":
                if open_req is not None:
                    # A newer request replaced it before any reply came, so the
                    # next reply belongs to the newer one: this one was never
                    # answered ("pending" is only for the end of the window).
                    out.append(_from_rows(open_req, None, "unanswered"))
                open_req = r
            elif r.message_kind in _REPLY_KINDS:
                if open_req is not None and r.timestamp - open_req.timestamp <= PAIR_WINDOW:
                    out.append(_from_rows(open_req, r, _state_for_reply(r.status)))
                else:
                    if open_req is not None:
                        out.append(_from_rows(open_req, None, "unanswered"))
                    out.append(_from_rows(None, r, "unexpected"))
                open_req = None
        if open_req is not None:
            out.append(_from_rows(open_req, None, _open_request_state(open_req.timestamp, now)))

    out.sort(key=lambda s: (s.started_at, s.request_event_id or s.reply_event_id or 0))
    return out


@dataclass
class CommandStats:
    component: Optional[str]
    command: Optional[str]
    total: int
    answered: int
    nak: int
    unanswered: int
    median_latency_ms: Optional[int]
    max_latency_ms: Optional[int]


def summarize(seqs: list[Sequence]) -> tuple[dict[str, int], list[CommandStats]]:
    counts = {s: 0 for s in STATES}
    groups: dict[tuple, list[Sequence]] = defaultdict(list)
    for s in seqs:
        counts[s.state] += 1
        groups[(s.component, s.command)].append(s)
    stats = []
    for (component, command), group in groups.items():
        lat = [s.latency_ms for s in group if s.latency_ms is not None]
        stats.append(CommandStats(
            component=component, command=command, total=len(group),
            answered=sum(s.state == "answered" for s in group), nak=sum(s.state == "nak" for s in group),
            unanswered=sum(s.state == "unanswered" for s in group),
            median_latency_ms=int(median(lat)) if lat else None, max_latency_ms=max(lat) if lat else None,
        ))
    stats.sort(key=lambda c: (-c.total, c.component or "", c.command or ""))
    return counts, stats


def find_sequences(db: Session, f: EventFilter, now: datetime) -> list[Sequence]:
    """Every sequence among the events matching `f` (time, device, ...).
    `now` (naive UTC) is the window end; pass f.until when the search has one."""
    q = (
        select(E.id, E.timestamp, E.device_id, E.component, E.message_kind, E.command,
               E.correlation_id, E.status)
        .where(*_conditions(f), E.component.in_(PAIRING.keys()),
               E.message_kind.in_(("request",) + _REPLY_KINDS))
        .order_by(E.timestamp, E.id)
        .limit(MAX_EXCHANGE_LINES + 1)
    )
    rows = db.execute(q).all()
    if len(rows) > MAX_EXCHANGE_LINES:
        raise FilterError("Too many request/reply lines in this range. Narrow the time range or pick devices.")
    return build_sequences(rows, now)
