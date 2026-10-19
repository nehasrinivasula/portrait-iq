"""Pydantic schemas — the API's request/response contracts. Keep these in
sync with models.py by hand for now; consider generating one from the other
once the shape stabilizes."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


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
