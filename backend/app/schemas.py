"""Pydantic schemas — the API's request/response contracts. Keep these in
sync with models.py by hand for now; consider generating one from the other
once the shape stabilizes."""
from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ConnectRequest(BaseModel):
    mode: str = "live"          # "live" | "pre_install"
    ip_address: str
    username: str
    password: str               # TODO: never log this, never persist it as-is


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
