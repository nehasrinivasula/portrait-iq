"""Shared fixtures. Every test gets its own throwaway SQLite file and staging
folder under tmp_path, so nothing touches backend/portrait_iq.db or data/."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  (registers tables on Base)
from app.config import settings
from app.db import Base

# Shaped like the real "all device log" export, values made up.
PORTRAIT_CSV = """timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description
"2026-10-01T02:00:00.393+0000","0","1","INFO","host-a","uuid-1","unsetFriendlyName","axone_logger[777:0]",":0","PerfData","{}"
"2026-10-01T02:00:01.500+0000","0","2","ERROR","host-b","uuid-2","unsetFriendlyName","SvcAdmDiagUI[1:0]",":0","General","Request/Response Timeout observed"
"2026-10-01T02:00:02.100+0000","0","3","WARN","host-c","uuid-3","H50","axonerouter[2:0]",":0","General","onSampleLost"
"""


@pytest.fixture(autouse=True)
def _job_files_in_tmp(tmp_path, monkeypatch):
    """Job output and uploads go to a temp folder, never backend/data, and
    no test touches a real cluster at startup."""
    monkeypatch.setattr(settings, "job_output_dir", str(tmp_path / "jobs"))
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "sweep_tool_pods_on_startup", False)


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def staging(tmp_path, monkeypatch):
    path = tmp_path / "staging"
    monkeypatch.setattr(settings, "staging_dir", str(path))
    return path