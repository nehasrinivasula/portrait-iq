"""Pydantic schemas — the API's request/response contracts. Keep these in
sync with models.py by hand for now; consider generating one from the other
once the shape stabilizes."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, SecretStr, field_validator

from app.secrets_loader import register_for_redaction


class ConnectRequest(BaseModel):
    mode: str = "live"          # "live" | "pre_install"
    ip_address: str
    username: str
    # Typed into the UI, kept in memory only, never saved (SCRUM-72).
    # SecretStr hides it in repr/str; registering it masks it in logs and
    # makes the export check catch it if it ever lands in a file.
    password: SecretStr

    @field_validator("password")
    @classmethod
    def _register_password(cls, v: SecretStr) -> SecretStr:
        register_for_redaction(v.get_secret_value(), "CONNECT_PASSWORD")
        return v


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
