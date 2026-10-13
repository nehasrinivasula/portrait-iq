"""Local-demo bearer-token authentication.

This module intentionally does not implement GE SSO. A future identity
provider can replace the credential check and token verification while routes
continue to depend on ``get_current_user``.
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

from app.config import settings

_last_seen: dict[str, float] = {}
_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class User:
    id: str
    name: str


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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo authentication is not configured.",
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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo authentication is not configured.",
        )

    _, _, secret = configured
    expires_at = int(time.time()) + settings.auth_token_ttl_seconds
    payload = {
        "sub": user.id,
        "exp": expires_at,
        "jti": secrets.token_urlsafe(16),
    }
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"v1.{encoded_payload}.{_sign(encoded_payload, secret)}", expires_at


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> User:
    """FastAPI dependency that verifies a local-demo bearer token."""
    if credentials is None:
        raise _unauthorized("Authentication required.")

    try:
        version, encoded_payload, signature = credentials.credentials.split(".")
        configured = _configured_demo_credentials()
        if configured is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Demo authentication is not configured.",
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
            or payload["exp"] < int(time.time())
        ):
            raise ValueError("invalid token")
    except HTTPException:
        raise
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        raise _unauthorized("Invalid or expired bearer token.") from None

    return User(id=username, name=username)


def touch_session(user_id: str) -> None:
    _last_seen[user_id] = time.time()


def is_locked(user_id: str) -> bool:
    last = _last_seen.get(user_id)
    if last is None:
        return False
    return (time.time() - last) > settings.session_idle_timeout_seconds
