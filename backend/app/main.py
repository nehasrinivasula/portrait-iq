import time

from fastapi import Depends, FastAPI, HTTPException, status

from app.config import settings
from app.db import init_db
from app.auth import User, authenticate_demo_user, create_access_token, get_current_user, touch_session
from app.orchestrator.routes import router as orchestrator_router
from app.schemas import CurrentUserOut, LoginRequest, TokenResponse

app = FastAPI(title=settings.app_name)
app.include_router(orchestrator_router, prefix="/api")


@app.post("/api/auth/login", response_model=TokenResponse)
def login(credentials: LoginRequest):
    user = authenticate_demo_user(credentials.username, credentials.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
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
