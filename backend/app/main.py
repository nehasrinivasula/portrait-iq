import time

from fastapi import Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import init_db
from app.auth import AuthError, User, authenticate_demo_user, create_access_token, get_current_user, touch_session
from app.orchestrator.routes import router as orchestrator_router
from app.schemas import CurrentUserOut, LoginRequest, TokenResponse
from app.secrets_loader import install_log_redaction
from app.sources.routes import router as sources_router
from app.storage.routes import router as logs_router

# Before anything logs, so every record (including startup) is masked. SCRUM-72.
install_log_redaction()

app = FastAPI(title=settings.app_name)
app.include_router(orchestrator_router, prefix="/api")
app.include_router(sources_router, prefix="/api/sources")
app.include_router(logs_router, prefix="/api/logs")


@app.exception_handler(AuthError)
async def _auth_error_handler(_: Request, exc: AuthError) -> JSONResponse:
    return JSONResponse(
        {"detail": exc.detail, "code": exc.code},
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422 repeats the submitted values ("input", and
    sometimes "ctx"), which for login and connect include the password.
    Keep where and why each field failed, never what was sent. SCRUM-72."""
    errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]
    return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)


@app.post("/api/auth/login", response_model=TokenResponse)
def login(credentials: LoginRequest):
    user = authenticate_demo_user(credentials.username, credentials.password)
    if user is None:
        raise AuthError(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid username or password.",
            "invalid_credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    touch_session(user.id)
    token, expires_at = create_access_token(user)
    return TokenResponse(
        access_token=token,
        expires_in=max(0, expires_at - int(time.time())),
    )


@app.get("/api/auth/me", response_model=CurrentUserOut)
def current_user(user: User = Depends(get_current_user)):
    return CurrentUserOut(username=user.id, name=user.name)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}
