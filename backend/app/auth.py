"""
Stub auth. Every request currently resolves to a single fake user, and the
session lock lives in a process-local dict — it disappears on restart and
doesn't work across more than one server process.

TODO before this is real:
  - Replace `get_current_user` with actual auth (GE HealthCare's own SSO /
    IdP, most likely — check with your GE sponsor before inventing one).
  - Move the session-lock state into the database or a shared cache (Redis)
    so it survives restarts and works if you ever run more than one worker.
  - Enforce the lock server-side on every request, not just in the frontend —
    the current HTML prototype's lock is JS-only and trivially bypassed.
"""
import time
from dataclasses import dataclass

from app.config import settings

_last_seen: dict[str, float] = {}


@dataclass
class User:
    id: str
    name: str


def get_current_user() -> User:
    """FastAPI dependency. Replace with real token verification."""
    return User(id="fe-service", name="Field engineer (stub)")


def touch_session(user_id: str) -> None:
    _last_seen[user_id] = time.time()


def is_locked(user_id: str) -> bool:
    last = _last_seen.get(user_id)
    if last is None:
        return False
    return (time.time() - last) > settings.session_idle_timeout_seconds
