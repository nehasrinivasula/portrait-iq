from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.orchestrator.routes import router as orchestrator_router
from app.secrets_loader import install_log_redaction

# Before anything logs, so every record (including startup) is masked. SCRUM-72.
install_log_redaction()

app = FastAPI(title=settings.app_name)
app.include_router(orchestrator_router, prefix="/api")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}
