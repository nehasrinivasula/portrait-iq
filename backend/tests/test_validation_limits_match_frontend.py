"""The limits the UI checks and the limits the API enforces must be the same numbers.

frontend/src/validation.ts and backend/app/validation.py each hold a copy; this reads both, so changing one
without the other fails here rather than showing up as an error the user cannot explain.
"""
import re
from pathlib import Path

from app import validation as backend
from app.storage.filters import MAX_TEXT_LENGTH

ROOT = Path(__file__).resolve().parents[2]
TS = (ROOT / "frontend" / "src" / "validation.ts").read_text(encoding="utf-8")


def ts_const(name: str) -> int:
    return int(re.search(rf"export const {name} = (\d+);", TS).group(1))


def test_the_text_limits_match():
    assert ts_const("MAX_USERNAME") == backend.MAX_USERNAME
    assert ts_const("MAX_PASSWORD") == backend.MAX_PASSWORD
    assert ts_const("MAX_FILTER_VALUE") == backend.MAX_FILTER_VALUE == MAX_TEXT_LENGTH
    assert ts_const("MAX_ID") == backend.MAX_ID
    assert ts_const("MAX_LABEL") == backend.MAX_LABEL
    assert ts_const("MAX_HOST") == backend.MAX_HOST


def test_the_import_file_types_match():
    from app.sources.base import ALLOWED_SUFFIXES
    listed = re.search(r"IMPORT_SUFFIXES = \[(.*?)\]", TS).group(1)
    assert set(re.findall(r'"(\.[a-z]+)"', listed)) == set(ALLOWED_SUFFIXES)


def test_the_login_request_uses_the_same_bounds():
    from app.schemas import LoginRequest
    fields = LoginRequest.model_fields
    assert fields["username"].metadata[1].max_length == backend.MAX_USERNAME
    assert fields["password"].metadata[1].max_length == backend.MAX_PASSWORD
