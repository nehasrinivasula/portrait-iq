from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import User, get_active_user, get_unlocked_user
from app.cleanup import service
from app.db import get_db

router = APIRouter()


class ItemOut(BaseModel):
    id: str
    kind: Literal["pod", "files"]
    label: str
    detail: str
    kept: bool
    active: bool


class GroupOut(BaseModel):
    job_id: str
    status: str | None
    items: list[ItemOut]


class StatusOut(BaseModel):
    state: Literal["clean", "preserved", "busy", "pending", "unchecked"]
    pods_error: str | None
    interrupted_runs: int
    pending_count: int
    groups: list[GroupOut]


class ChoiceIn(BaseModel):
    action: Literal["remove", "keep"]
    context: Literal["logout", "restart", "manual"]
    items: list[str] = Field(min_length=1, max_length=service.MAX_ITEMS)


class OutcomeOut(BaseModel):
    id: str
    result: Literal["removed", "kept", "skipped", "failed"]
    reason: str | None = None


class ChoiceOut(BaseModel):
    outcomes: list[OutcomeOut]
    status: StatusOut


def _status_out(s: service.Status) -> StatusOut:
    return StatusOut(
        state=s.state, pods_error=s.pods_error, interrupted_runs=s.interrupted_runs,
        pending_count=len(s.pending),
        groups=[GroupOut(job_id=g.job_id, status=g.status, items=[ItemOut(**i.__dict__) for i in g.items])
                for g in s.groups],
    )


@router.get("", response_model=StatusOut)
def get_cleanup_status(db: Session = Depends(get_db), user: User = Depends(get_unlocked_user)):
    """What is left on the system, per run, and whether it needs a choice.
    The page asks this by itself after sign-in, so it does not count as activity."""
    return _status_out(service.current_status(db))


@router.post("", response_model=ChoiceOut)
def choose_cleanup(req: ChoiceIn, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    """Remove or keep the chosen items. Audited as cleanup_choice."""
    try:
        outcomes, status = service.apply_choice(
            db, actor=user.id, session_id=user.session_id, action=req.action, ids=req.items, context=req.context,
        )
    except service.CleanupError as e:
        raise HTTPException(400, str(e))
    return ChoiceOut(outcomes=[OutcomeOut(**o.__dict__) for o in outcomes], status=_status_out(status))
