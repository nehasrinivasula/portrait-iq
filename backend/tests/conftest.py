import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _job_files_in_tmp(tmp_path, monkeypatch):
    """Job output and uploads go to a temp folder, never backend/data, and
    no test touches a real cluster at startup."""
    monkeypatch.setattr(settings, "job_output_dir", str(tmp_path / "jobs"))
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "sweep_tool_pods_on_startup", False)
