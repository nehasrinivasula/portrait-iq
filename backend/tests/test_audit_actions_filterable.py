"""Every action the backend records must be selectable in the audit page's Action filter.

The expected set is read from the backend source (every log_action call with a
literal action name), not from a hand-kept list, so a new action that is not
added to ACTION_LABELS in frontend/src/features/audit/auditApi.ts fails here.
"""
import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
LABELS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / "audit" / "auditApi.ts"


def recorded_actions() -> dict[str, str]:
    """Literal action name -> where it is recorded, for every log_action(...) call."""
    found: dict[str, str] = {}
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (getattr(fn, "id", None) or getattr(fn, "attr", None)) != "log_action":
                continue
            action = next((kw.value for kw in node.keywords if kw.arg == "action"), None)
            if action is None and len(node.args) >= 3:       # log_action(db, actor, action, ...)
                action = node.args[2]
            if isinstance(action, ast.Constant) and isinstance(action.value, str):
                found.setdefault(action.value, f"{path.relative_to(APP.parent)}:{node.lineno}")
    return found


def offered_actions() -> set[str]:
    text = LABELS.read_text(encoding="utf-8")
    block = text[text.index("const ACTION_LABELS"):]
    block = block[: block.index("\n};")]
    return set(re.findall(r"^\s{2}([a-z_]+):", block, flags=re.M))


def test_the_scan_finds_the_known_actions():
    found = recorded_actions()
    assert {"login", "session_lock", "summary_view", "findings_run"} <= set(found)


def test_every_recorded_action_can_be_filtered_to():
    missing = {a: where for a, where in recorded_actions().items() if a not in offered_actions()}
    assert not missing, f"Add these to ACTION_LABELS in auditApi.ts: {missing}"
