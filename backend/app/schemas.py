"""Pydantic schemas — the API's request/response contracts. Keep these in
sync with models.py by hand for now; consider generating one from the other
once the shape stabilizes."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, SecretStr


class ConnectRequest(BaseModel):
    mode: str = "live"          # "live" | "pre_install"
    ip_address: str
    username: str
    # Typed into the UI, kept in memory only, never saved (SCRUM-72).
    # SecretStr hides it in repr/str. The connect route masks it in logs
    # and the export check for as long as the request runs
    # (secrets_loader.redaction_scope), then forgets it.
    password: SecretStr


class DeviceOut(BaseModel):
    id: str
    name: str
    kind: str
    ip_address: str
    reachable: bool
    overridden: bool

    class Config:
        from_attributes = True


class JobCreateRequest(BaseModel):
    mode: str = "live"
    tools: list[str] = []                 # e.g. ["netshoot", "rti"]
    tshark_mode: Optional[str] = None      # "run" | "import" | None
    cpu_limit_pct: Optional[int] = None
    mem_limit_mb: Optional[int] = None
    max_output_mb: Optional[int] = None


class JobOut(BaseModel):
    id: str
    mode: str
    status: str
    current_step: Optional[str]
    created_at: datetime
    finished_at: Optional[datetime]

    class Config:
        from_attributes = True


class SignalOut(BaseModel):
    id: str
    rule_id: str
    title: str
    severity: str
    count: int
    status: str
    manual_ref: Optional[str]
    actions: list[str] = []

    class Config:
        from_attributes = True


# --- Log acquisition (SCRUM-43) ---

class SourceOut(BaseModel):
    id: str
    display_name: str
    needs_network: bool
    configured: bool
    reason: str = ""                     # why it is unavailable, when configured is False
    options: dict[str, list[str]] = {}


class SourceStatusOut(BaseModel):
    ok: bool
    reason: str = ""


class AcquireRequest(BaseModel):
    # Both optional: default is the full allowed window (last 30 days).
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    # Source-specific choices, e.g. {"path": "/Volumes/USB/logs"} for local import.
    options: dict[str, str] = {}
    job_id: Optional[str] = None
    ingest: bool = True


class RemoteFileOut(BaseModel):
    remote_id: str
    name: str
    size: Optional[int]
    modified_at: Optional[datetime]
    already_acquired: bool


class FileOutcomeOut(BaseModel):
    name: str
    status: str
    size: Optional[int] = None
    events: Optional[int] = None
    error: Optional[str] = None


class AcquisitionOut(BaseModel):
    run_id: str
    source: str
    since: datetime
    until: datetime
    new_files: int
    already_acquired: int
    failed: int
    over_budget: int
    bytes_staged: int
    files: list[FileOutcomeOut]
