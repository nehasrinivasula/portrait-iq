from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.orchestrator.routes import router as orchestrator_router

app = FastAPI(title=settings.app_name)
app.include_router(orchestrator_router, prefix="/api")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}
