"""Pydantic schemas — the API's request/response contracts. Keep these in
sync with models.py by hand for now; consider generating one from the other
once the shape stabilizes."""
from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, SecretStr


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CurrentUserOut(BaseModel):
    username: str
    name: str


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


TcpPort = Annotated[int, Field(ge=1, le=65535)]


class ConnectivityCheckRequest(BaseModel):
    """A bounded TCP preflight check performed before diagnostics start."""

    target: str = Field(min_length=1, max_length=253)
    ports: list[TcpPort] = Field(min_length=1, max_length=16)
    timeout_seconds: float = Field(default=3.0, ge=0.1, le=10.0)

    @field_validator("target")
    @classmethod
    def normalize_target(cls, value: str) -> str:
        target = value.strip()
        if not target or any(character.isspace() for character in target):
            raise ValueError("target must be an IP address or hostname without whitespace")
        return target

    @field_validator("ports")
    @classmethod
    def reject_duplicate_ports(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("ports must not contain duplicates")
        return value


class ConnectivityPortResult(BaseModel):
    port: int
    status: Literal[
        "reachable",
        "connection_refused",
        "timeout",
        "host_unreachable",
        "network_unreachable",
        "blocked_by_local_policy",
        "connection_error",
        "dns_failure",
    ]
    message: str
    latency_ms: Optional[float] = None


class ConnectivityCheckResponse(BaseModel):
    target: str
    resolved_addresses: list[str]
    status: Literal["reachable", "partially_reachable", "unreachable"]
    reachable: bool
    summary: str
    results: list[ConnectivityPortResult]


class JobCreateRequest(BaseModel):
    # Unknown fields are an error, not silently ignored: a client still
    # sending the old tshark_mode gets a 422 naming it, instead of a job
    # that quietly runs with defaults.
    model_config = ConfigDict(extra="forbid")

    mode: str = "live"
    tools: list[str] = []                 # plugin names, e.g. ["netshoot", "tshark"]
    # Per-plugin options, keyed by plugin name, e.g. {"tshark": {"mode": "import"}}.
    # Each plugin's configure() validates its own entry.
    plugin_options: dict[str, dict[str, Any]] = {}
    cpu_limit_pct: Optional[int] = None
    mem_limit_mb: Optional[int] = None
    max_output_mb: Optional[int] = None


class PluginOut(BaseModel):
    name: str
    display_name: str
    description: str


class PluginListOut(BaseModel):
    plugins: list[PluginOut]
    load_errors: dict[str, str] = {}   # plugins skipped at startup, and why


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
