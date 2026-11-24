"""deploy/scripts/image-tag.sh: the build tag must be stable for identical
content, change for any change to the image's build context, and ignore
changes outside it (SCRUM, PR #68 review)."""
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
    (r / "frontend").mkdir()
    (r / "backend" / "app" / "main.py").write_text("print('v1')\n")
    (r / "frontend" / "ui.ts").write_text("export const x = 1\n")
    (r / "README.md").write_text("# repo\n")
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def test_clean_tree_has_no_dirty_suffix(repo: Path):
    tag = _tag(repo)
    assert tag and "-dirty-" not in tag
    assert tag == _tag(repo)  # and it is stable


def test_identical_dirty_content_hashes_the_same_across_a_time_boundary(repo: Path):
    # Finding 1: git stash create embeds a timestamp, so it drifted second to
    # second. A tree hash must not: the same dirty tree is the same tag.
    (repo / "backend" / "app" / "main.py").write_text("print('v2 edited')\n")
    first = _tag(repo)
    time.sleep(1.1)  # cross a whole-second boundary
    second = _tag(repo)
    assert "-dirty-" in first
    assert first == second, f"tag drifted over time: {first} != {second}"


def test_untracked_backend_file_changes_the_tag(repo: Path):
    # Finding 2: a new source file docker would COPY must change the tag even
    # before it is `git add`ed.
    clean = _tag(repo)
    (repo / "backend" / "app" / "new_collector.py").write_text("x = 1\n")
    dirty = _tag(repo)
    assert "-dirty-" in dirty
    assert dirty != clean


def test_edit_outside_the_backend_context_does_not_change_the_tag(repo: Path):
    # Finding 3: the image is built from backend/ only, so a frontend or docs
    # edit must not churn the image tag.
    clean = _tag(repo)
    (repo / "frontend" / "ui.ts").write_text("export const x = 999\n")
    (repo / "README.md").write_text("# repo\n\nmore docs\n")
    assert _tag(repo) == clean


def test_distinct_backend_changes_get_distinct_tags(repo: Path):
    (repo / "backend" / "app" / "main.py").write_text("print('a')\n")
    a = _tag(repo)
    (repo / "backend" / "app" / "main.py").write_text("print('b')\n")
    b = _tag(repo)
    assert a != b and "-dirty-" in a and "-dirty-" in b
