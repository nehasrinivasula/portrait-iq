"""Pattern detection after logs arrive.

When an acquisition stored new events for a collection, the rules run on that collection in the
background, so findings are there without anyone pressing Run. It never makes the acquisition fail
or wait: the request returns first, an error is recorded (type only) and audited, and the manual
POST /api/jobs/{id}/findings/run is the retry.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.analysis.engine import run_analysis
from app.audit import log_action
from app.models import AnalysisRun

log = logging.getLogger(__name__)


def should_run(job_id, outcomes) -> bool:
    """A collection id and at least one file whose events were stored."""
    return bool(job_id) and any(getattr(o, "status", None) == "ingested" for o in outcomes)


def run_in_background(bind, job_id: str, actor: str, session_id) -> None:
    """Run the rules for one collection on a session of its own (the request's is closed by now).
    `bind` is the engine the request used, so tests and the app each run against their own database."""
    db: Session = sessionmaker(bind=bind)()
    try:
        log_action(db, actor, "findings_run", detail=f"job={job_id} auto=true", session_id=session_id)
        db.commit()
        run_analysis(db, job_id)
    except Exception as e:  # noqa: BLE001 - nothing here may reach the caller; the type is recorded below
        log.exception("automatic pattern detection for %s failed", job_id)
        db.rollback()
        try:
            _mark(db, job_id, "failed", error=type(e).__name__)
            log_action(db, actor, "findings_run_failed", detail=f"job={job_id} error={type(e).__name__}",
                       session_id=session_id)
            db.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not record the failed analysis run for %s", job_id)
            db.rollback()
    finally:
        db.close()


def _mark(db: Session, job_id: str, status: str, error=None) -> None:
    db.merge(AnalysisRun(job_id=job_id, status=status, ran_at=datetime.utcnow(), events_scanned=None, error=error))
    db.flush()
