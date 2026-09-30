from sqlalchemy.orm import Session

from app.models import AuditLogEntry


def log_action(db: Session, actor: str, action: str, detail: str = "") -> None:
    """Call this at every state change: connect, discover, job start/stop,
    tool selection, session lock/unlock, package download. Cheap to call
    too often; expensive to be missing one when someone asks 'who ran this'."""
    entry = AuditLogEntry(actor=actor, action=action, detail=detail)
    db.add(entry)
    db.commit()
