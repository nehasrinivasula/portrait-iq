"""
ORM tables. Five tables, one per architecture-diagram box that needs to
persist state: Device (connect), Job (orchestrator), NormalizedEvent
(event store), Signal (analysis engine output), AuditLogEntry (cross-cutting).
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Device(Base):
    """A component discovered or manually entered during Connect.
    Mirrors Figure 1: viewer / hub / server / student laptop."""
    __tablename__ = "devices"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)          # e.g. "Server / MiniEdge P360"
    kind = Column(String, nullable=False)           # "kubernetes_node" | "hub" | "viewer" | ...
    ip_address = Column(String, nullable=False)
    reachable = Column(Integer, default=0)          # 0/1, set by discovery
    overridden = Column(Integer, default=0)          # 1 if a user edited an auto-filled value
    created_at = Column(DateTime, default=datetime.utcnow)


class Job(Base):
    """One collection run. mode is 'live' or 'pre_install', matching the
    Connect step's two-mode choice from the EU team's feedback."""
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=_uuid)
    mode = Column(String, nullable=False, default="live")     # "live" | "pre_install"
    status = Column(String, nullable=False, default="queued")  # queued|running|done|failed
    current_step = Column(String, nullable=True)
    cpu_limit_pct = Column(Integer, nullable=True)
    mem_limit_mb = Column(Integer, nullable=True)
    max_output_mb = Column(Integer, nullable=True)
    tools = Column(Text, nullable=True)              # JSON list, e.g. ["netshoot","rti"]
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    events = relationship("NormalizedEvent", back_populates="job")
    signals = relationship("Signal", back_populates="job")


class NormalizedEvent(Base):
    """The one event shape everything downstream reads, regardless of which
    parser produced it. This table IS the 'event store' box in the diagram."""
    __tablename__ = "normalized_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=True)  # null for ad-hoc ingests
    timestamp = Column(DateTime, nullable=False, index=True)
    device_id = Column(String, nullable=True, index=True)         # hostName / friendlyName from source
    source = Column(String, nullable=False)             # "portrait" | "compute" | "network"
    source_file = Column(String, nullable=True, index=True)
    source_line = Column(Integer, nullable=True)
    category = Column(String, nullable=True)              # e.g. "General", "PerfData"
    severity = Column(String, nullable=False)               # INFO | WARN | ERROR
    process = Column(String, nullable=True)
    message = Column(Text, nullable=False)

    job = relationship("Job", back_populates="events")


class Signal(Base):
    """Output of the analysis engine: a pattern that fired, plus (eventually)
    a knowledge-layer match. status starts 'unconfirmed' and stays that way
    until knowledge.lookup finds a real manual entry — see Marco's feedback,
    this must never silently invent a confirmed match."""
    __tablename__ = "signals"

    id = Column(String, primary_key=True, default=_uuid)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    rule_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    severity = Column(String, nullable=False)   # "error" | "warning" | "info"
    count = Column(Integer, default=0)
    status = Column(String, default="unconfirmed")  # "unconfirmed" | "confirmed" | "no_match"
    manual_ref = Column(String, nullable=True)
    actions_json = Column(Text, nullable=True)   # JSON list of suggested next steps

    job = relationship("Job", back_populates="signals")


class AuditLogEntry(Base):
    """Every action, per the requirements doc's 'all tool actions and
    configuration changes to be logged' line."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    actor = Column(String, nullable=False)     # user id — real once auth.py is real
    action = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
