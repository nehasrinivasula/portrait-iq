"""
ORM tables. One per architecture-diagram box that needs to persist state:
Device (connect), Job (orchestrator), NormalizedEvent (event store), Signal
(analysis engine output), AcquiredFileRecord (log acquisition manifest),
IngestedFile (receipt of what ingestion stored per file), AuditLogEntry (cross-cutting).
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
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
    status = Column(String, nullable=False, default="queued")  # queued|running|done|partial|failed|cleanup_failed|interrupted
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
    device_id = Column(String, nullable=True)                     # hostName / friendlyName from source
    source = Column(String, nullable=False)             # "portrait" | "compute" | "network"
    source_file = Column(String, nullable=True, index=True)
    source_line = Column(Integer, nullable=True)
    category = Column(String, nullable=True)              # e.g. "General", "PerfData"
    severity = Column(String, nullable=False)               # INFO | WARN | ERROR
    process = Column(String, nullable=True)
    message = Column(Text, nullable=False)

    job = relationship("Job", back_populates="events")

    # Search is by time, usually inside one collection or one device. These
    # keep those queries fast at hundreds of thousands of rows. (job_id,
    # timestamp, id) matches the paging order of the unified view exactly.
    __table_args__ = (
        Index("ix_events_job_ts", "job_id", "timestamp", "id"),
        # Also serves plain device_id lookups, so device_id has no index of its own.
        Index("ix_events_device_ts", "device_id", "timestamp"),
    )


class IngestedFile(Base):
    """One row per file the pipeline stored, written in the same transaction
    as that file's events. It is the receipt the unified view checks against:
    rows read, events stored, rows rejected. Without it, "every entry is
    visible" could not be told apart from "every entry we happened to keep".
    Keyed like the events: (source_file, job_id)."""
    __tablename__ = "ingested_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_file = Column(String, nullable=False, index=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=True, index=True)
    parser = Column(String, nullable=True)
    rows_read = Column(Integer, nullable=False, default=0)
    events_written = Column(Integer, nullable=False, default=0)
    rows_rejected = Column(Integer, nullable=False, default=0)
    # Events deliberately deleted after ingest (e.g. duplicates), via
    # storage.removal.remove_events. Keeps rows_read == events_written +
    # rows_rejected true forever; events now expected = written - removed.
    events_removed = Column(Integer, nullable=False, default=0)
    reject_reasons = Column(Text, nullable=True)     # JSON {reason: count}; short constants, never row text
    first_timestamp = Column(DateTime, nullable=True)
    last_timestamp = Column(DateTime, nullable=True)
    ingested_at = Column(DateTime, default=datetime.utcnow)


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


class AcquiredFileRecord(Base):
    """Manifest of every file pulled from a log source (SCRUM-43). This is
    what makes "only pull new, unprocessed files" true: before fetching, the
    acquisition service skips anything already recorded here as a success.

    Identity is (source, remote_id, remote_checksum), so a file that changed
    at the source (new Box version, re-exported log) counts as new. A
    'failed' row is retried on the next run instead of being skipped."""
    __tablename__ = "acquired_files"
    __table_args__ = (Index("ix_acquired_identity", "source", "remote_id", "remote_checksum"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)            # LogSource.id: "local" | "box" | ...
    remote_id = Column(String, nullable=False)          # Box file id, sha256 for local files, ...
    remote_checksum = Column(String, nullable=True)       # checksum the source reported, if any
    name = Column(String, nullable=False)
    size = Column(Integer, nullable=True)
    remote_modified_at = Column(DateTime, nullable=True)
    # For export-style sources (Portrait diags): which service/device and
    # time window this file covers, so the next pull can start where it ended.
    scope = Column(String, nullable=True)
    window_start = Column(DateTime, nullable=True)
    window_end = Column(DateTime, nullable=True)
    sha256 = Column(String, nullable=True)               # of the bytes we actually downloaded
    local_path = Column(String, nullable=True)
    status = Column(String, nullable=False)    # acquired | ingested | unrecognized | duplicate | failed
    events = Column(Integer, nullable=True)     # rows ingested, when ingestion ran
    error = Column(Text, nullable=True)          # user-facing reason, never contains secrets
    job_id = Column(String, ForeignKey("jobs.id"), nullable=True)
    acquired_at = Column(DateTime, default=datetime.utcnow)


class AuditLogEntry(Base):
    """Every action, per the requirements doc's 'all tool actions and
    configuration changes to be logged' line."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    actor = Column(String, nullable=False)     # user id — real once auth.py is real
    action = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
