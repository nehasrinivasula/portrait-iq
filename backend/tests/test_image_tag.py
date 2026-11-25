"""deploy/scripts/image-tag.sh: the build tag is a content hash of exactly the
backend image inputs (Dockerfile + requirements.txt + app/). It must be stable
for identical inputs, change for any change to those inputs, and ignore
everything the image does not contain -- other commits, the frontend, docs, and
backend/tests/ (SCRUM, PR #68 review)."""
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "scripts" / "image-tag.sh"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _tag(repo: Path, ctx: str = "backend") -> str:
    out = subprocess.run(["sh", str(SCRIPT), ctx], cwd=repo,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if not shutil.which("git"):
        pytest.skip("git not available")
    r = tmp_path / "repo"
    (r / "backend" / "app").mkdir(parents=True)
    (r / "backend" / "tests").mkdir()
    (r / "frontend").mkdir()
    (r / "backend" / "Dockerfile").write_text("FROM python:3.13\nCOPY requirements.txt .\nCOPY app ./app\n")
    (r / "backend" / "requirements.txt").write_text("fastapi\n")
    (r / "backend" / "app" / "main.py").write_text("print('v1')\n")
    (r / "backend" / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    (r / "frontend" / "ui.ts").write_text("export const x = 1\n")
    (r / "README.md").write_text("# repo\n")
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def test_tag_is_nonempty_and_stable(repo: Path):
    tag = _tag(repo)
    assert tag
    assert tag == _tag(repo)


def test_identical_inputs_hash_the_same_across_a_time_boundary(repo: Path):
    # A tree hash carries no commit timestamp, so the tag must not drift second
    # to second the way `git stash create` did.
    (repo / "backend" / "app" / "main.py").write_text("print('v2 edited')\n")
    first = _tag(repo)
    time.sleep(1.1)  # cross a whole-second boundary
    second = _tag(repo)
    assert first == second, f"tag drifted over time: {first} != {second}"


@pytest.mark.parametrize("path,content", [
    ("backend/app/main.py", "print('changed')\n"),
    ("backend/requirements.txt", "fastapi\nuvicorn\n"),
    ("backend/Dockerfile", "FROM python:3.13\nENV X=1\nCOPY requirements.txt .\nCOPY app ./app\n"),
])
def test_editing_an_image_input_changes_the_tag(repo: Path, path: str, content: str):
    before = _tag(repo)
    (repo / path).write_text(content)
    assert _tag(repo) != before


def test_untracked_app_file_changes_the_tag(repo: Path):
    # A new source file docker would COPY must change the tag even before it is
    # `git add`ed.
    before = _tag(repo)
    (repo / "backend" / "app" / "new_collector.py").write_text("x = 1\n")
    assert _tag(repo) != before


def test_a_committed_frontend_only_change_does_not_change_the_tag(repo: Path):
    # The image is not built from the frontend or docs, so a commit that only
    # touches them must not change the tag or force a backend rollout (finding 1).
    before = _tag(repo)
    (repo / "frontend" / "ui.ts").write_text("export const x = 999\n")
    (repo / "README.md").write_text("# repo\n\nmore docs\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "frontend and docs only")
    assert _tag(repo) == before


def test_editing_backend_tests_does_not_change_the_tag(repo: Path):
    # .dockerignore excludes backend/tests/, so it is not in the image; editing
    # it must not change the tag (finding 2).
    before = _tag(repo)
    (repo / "backend" / "tests" / "test_x.py").write_text("def test_x(): assert True\n")
    assert _tag(repo) == before
