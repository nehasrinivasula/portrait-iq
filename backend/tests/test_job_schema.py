"""JobOut times carry a UTC offset, so the browser's run timer (SCRUM-41)
does not read them as local time."""
from datetime import datetime

from app.models import Job
from app.schemas import JobOut


def test_job_times_are_sent_as_utc():
    job = Job(id="j1", mode="live", status="done", current_step="package",
              created_at=datetime(2026, 11, 5, 10, 0, 0), finished_at=datetime(2026, 11, 5, 10, 3, 5))
    out = JobOut.model_validate(job).model_dump(mode="json")
    assert out["created_at"] == "2026-11-05T10:00:00Z"
    assert out["finished_at"] == "2026-11-05T10:03:05Z"
    assert out["current_step"] == "package"


def test_running_job_has_no_finish_time():
    job = Job(id="j2", mode="live", status="running", current_step="deploy_tools",
              created_at=datetime(2026, 11, 5, 10, 0, 0))
    out = JobOut.model_validate(job).model_dump(mode="json")
    assert out["created_at"].endswith("Z")
    assert out["finished_at"] is None
