"""Local-demo bearer-token authentication.

This module intentionally does not implement GE SSO. A future identity
provider can replace the credential check and token verification in
``get_current_user``. Routes depend on ``get_active_user`` or
``get_unlocked_user``, which add the server-side idle lock on top of it.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.audit import log_action
from app.config import settings
from app.db import get_db
from app.models import SessionActivity

_bearer_scheme = HTTPBearer(auto_error=False)


class AuthError(HTTPException):
    """HTTPException carrying a machine-readable ``code`` the frontend switches on."""

    def __init__(self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


NOT_CONFIGURED_DETAIL = "Demo authentication is not configured."


@dataclass
class User:
    id: str
    name: str
    # The signed token's ``jti`` identifies one login session. It is never
    # returned by the public user endpoint or accepted from a client request.
    session_id: str | None = None


def _configured_demo_credentials() -> tuple[str, str, str] | None:
    """Return local-demo credentials only when every required env value exists."""
    if (
        settings.demo_auth_username is None
        or settings.demo_auth_password is None
        or settings.auth_token_secret is None
    ):
        return None
    username = settings.demo_auth_username
    password = settings.demo_auth_password.get_secret_value()
    secret = settings.auth_token_secret.get_secret_value()
    if not all((username, password, secret)):
        return None
    return username, password, secret


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(encoded_payload: str, secret: str) -> str:
    return _b64encode(
        hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    )


def authenticate_demo_user(username: str, password: str) -> User | None:
    """Validate local-demo credentials from environment variables."""
    configured = _configured_demo_credentials()
    if configured is None:
        raise AuthError(
            status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED_DETAIL, "not_configured"
        )

    configured_username, configured_password, _ = configured
    if not (
        hmac.compare_digest(username, configured_username)
        and hmac.compare_digest(password, configured_password)
    ):
        return None
    return User(id=configured_username, name=configured_username)


def create_access_token(user: User) -> tuple[str, int]:
    """Create a signed, expiring token for the local demo."""
    configured = _configured_demo_credentials()
    if configured is None:
        raise AuthError(
            status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED_DETAIL, "not_configured"
        )

    _, _, secret = configured
    expires_at = int(time.time()) + settings.auth_token_ttl_seconds
    session_id = user.session_id or secrets.token_urlsafe(16)
    # Keep it on the server-side request user so the login route can audit
    # the session it just issued without re-parsing its own token.
    user.session_id = session_id
    payload = {
        "sub": user.id,
        "exp": expires_at,
        "jti": session_id,
    }
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"v1.{encoded_payload}.{_sign(encoded_payload, secret)}", expires_at


def _unauthorized(detail: str, code: str) -> AuthError:
    return AuthError(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        code,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> User:
    """FastAPI dependency that verifies a local-demo bearer token."""
    if credentials is None:
        raise _unauthorized("Authentication required.", "auth_required")

    try:
        version, encoded_payload, signature = credentials.credentials.split(".")
        configured = _configured_demo_credentials()
        if configured is None:
            raise AuthError(
                status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED_DETAIL, "not_configured"
            )
        username, _, secret = configured
        expected_signature = _sign(encoded_payload, secret)
        payload = json.loads(_b64decode(encoded_payload))
        if (
            version != "v1"
            or not hmac.compare_digest(signature, expected_signature)
            or not isinstance(payload.get("sub"), str)
            or not hmac.compare_digest(payload["sub"], username)
            or not isinstance(payload.get("exp"), int)
            or not isinstance(payload.get("jti"), str)
            or not payload["jti"]
            or payload["exp"] < int(time.time())
        ):
            raise ValueError("invalid token")
    except HTTPException:
        raise
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        raise _unauthorized("Invalid or expired bearer token.", "token_invalid") from None

    return User(id=username, name=username, session_id=payload["jti"])


# Idle lock (S-2, SCRUM-34) ---------------------------------------------------
#
# Enforced on the server, per login session (token ``jti``), with the last
# activity stored in the database so neither a page refresh nor a backend
# restart unlocks an idle session. Every protected route checks the lock;
# only user-initiated routes count as activity:
#
#   get_unlocked_user  check only. For requests the page makes on its own
#                      (job polling, restoring the session on refresh), so an
#                      open page left alone still locks.
#   get_active_user    check, then record activity. Everything else.
#
# Logging in again issues a new session, which is how a locked user unlocks.

SESSION_LOCKED_DETAIL = "Session locked. Log in again to unlock."


def start_session(db: Session, user: User) -> None:
    """Record a new login session as active now. Call after the token is
    issued, so ``user.session_id`` is set. Also drops rows whose tokens
    have certainly expired, so the table does not grow without bound."""
    now = time.time()
    horizon = max(settings.auth_token_ttl_seconds, settings.session_idle_timeout_seconds)
    db.query(SessionActivity).filter(SessionActivity.last_active_at < now - horizon).delete()
    db.merge(SessionActivity(session_id=user.session_id, user_id=user.id, last_active_at=now))
    db.commit()


def touch_session(db: Session, user: User) -> None:
    """Count a user-initiated request as activity. Never revives a locked
    session."""
    if not user.session_id:
        return
    db.query(SessionActivity).filter(
        SessionActivity.session_id == user.session_id,
        SessionActivity.locked_at.is_(None),
    ).update({SessionActivity.last_active_at: time.time()})
    db.commit()


def is_locked(db: Session, user: User) -> bool:
    """True when this login session is locked; locks it (and audits the
    lock, once) when it has just gone idle.

    Fails closed: a validly signed token whose session has no activity
    record is locked. Login always writes the record before returning the
    token, so the only ways to get here are a lost database or a session
    the server never issued, and neither should let a request through."""
    if not user.session_id:
        return True
    now = time.time()
    row = db.get(SessionActivity, user.session_id)
    if row is not None and row.locked_at is not None:
        return True
    if row is None:
        reason = "reason=no_activity_record"
        db.add(SessionActivity(session_id=user.session_id, user_id=user.id,
                               last_active_at=now, locked_at=now))
    elif now - row.last_active_at > settings.session_idle_timeout_seconds:
        reason = f"reason=idle idle_seconds={int(now - row.last_active_at)}"
        row.locked_at = now
    else:
        return False
    db.commit()
    log_action(db, user.id, "session_lock", detail=reason, session_id=user.session_id)
    return True


def get_unlocked_user(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    """Refuse a locked session without counting the request as activity.
    For requests the page makes by itself, e.g. polling a job."""
    if is_locked(db, user):
        raise AuthError(423, SESSION_LOCKED_DETAIL, "session_locked")
    return user


def get_active_user(user: User = Depends(get_unlocked_user), db: Session = Depends(get_db)) -> User:
    """Refuse a locked session, otherwise count the request as activity.
    The default for protected routes."""
    touch_session(db, user)
    return user
