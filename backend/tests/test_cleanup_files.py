"""Item ids and job folders for the cleanup choice. No database or cluster."""
import os

import pytest

from app.cleanup.files import (
    CleanupError, clear_job_files, folder_stats, human_size, parse_ids,
    prune_old_runs, safe_job_dir,
)
from app.config import settings

_MB = 1024 * 1024


def _run(jobs_dir, name, *, size=0, mtime=0.0):
    """A run folder with a known size and mtime (mtime set last, after the write)."""
    d = jobs_dir / name
    d.mkdir()
    if size:
        (d / "artifact.bin").write_bytes(b"x" * size)
    os.utime(d, (mtime, mtime))
    return d


@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    path = tmp_path / "jobs"
    path.mkdir()
    monkeypatch.setattr(settings, "job_output_dir", str(path))
    return path


def test_parse_ids_accepts_pods_and_files_and_drops_duplicates():
    assert parse_ids(["pod:piq-tshark-ab12-cd34", "files:0a1b-2c", "pod:piq-tshark-ab12-cd34"]) == [
        "pod:piq-tshark-ab12-cd34", "files:0a1b-2c"]


@pytest.mark.parametrize("bad", ["", "pod:", "pod:../x", "files:a/b", "files:..", "files:../../etc", "job:abc",
                                 "pod:UPPER lower", "pod:a;rm", "files:" + "x" * 129, "pod:-lead", "pod:a\nb"])
def test_parse_ids_rejects_anything_that_is_not_a_plain_name(bad):
    with pytest.raises(CleanupError):
        parse_ids([bad])


def test_parse_ids_needs_at_least_one_and_caps_the_batch():
    with pytest.raises(CleanupError, match="at least one"):
        parse_ids([])
    with pytest.raises(CleanupError, match="at most"):
        parse_ids([f"pod:p{i}" for i in range(201)])


@pytest.mark.parametrize("n,text", [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"), (5 * 1024 * 1024, "5.0 MB"),
                                    (3 * 1024 ** 3, "3.0 GB"), (5000 * 1024 ** 3, "5000.0 GB")])
def test_human_size(n, text):
    assert human_size(n) == text


def test_folder_stats_counts_files_in_subfolders_and_skips_symlinks(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.pcap").write_bytes(b"x" * 10)
    (tmp_path / "two.json").write_bytes(b"y" * 5)
    (tmp_path / "link").symlink_to(tmp_path / "two.json")
    assert folder_stats(tmp_path) == (2, 15)
    assert folder_stats(tmp_path / "missing") == (0, 0)


def test_safe_job_dir_only_returns_real_folders_inside_the_job_output_folder(jobs_dir, tmp_path):
    (jobs_dir / "job1").mkdir()
    assert safe_job_dir("job1") == (jobs_dir / "job1").resolve()
    assert safe_job_dir("nope") is None
    (tmp_path / "outside").mkdir()
    assert safe_job_dir("../outside") is None
    (jobs_dir / "escape").symlink_to(tmp_path / "outside")
    assert safe_job_dir("escape") is None
    (jobs_dir / "afile").write_text("x")
    assert safe_job_dir("afile") is None


def test_the_export_folder_is_not_counted_and_not_deleted(tmp_path):
    (tmp_path / "tshark").mkdir()
    (tmp_path / "tshark" / "a.pcap").write_bytes(b"x" * 10)
    (tmp_path / "results.json").write_bytes(b"{}")
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "export.zip").write_bytes(b"z" * 500)
    assert folder_stats(tmp_path) == (2, 12)
    clear_job_files(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["package"]
    assert (tmp_path / "package" / "export.zip").read_bytes() == b"z" * 500


def test_a_folder_with_only_an_export_has_nothing_to_clean(tmp_path):
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "export.zip").write_bytes(b"z")
    assert folder_stats(tmp_path) == (0, 0)


def test_only_the_top_level_package_folder_is_protected(tmp_path):
    (tmp_path / "tshark" / "package").mkdir(parents=True)
    (tmp_path / "tshark" / "package" / "a.pcap").write_bytes(b"x")
    assert folder_stats(tmp_path) == (1, 1)
    clear_job_files(tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- run retention (SCRUM-105) -------------------------------------------

def test_prune_disabled_when_keep_and_cap_both_zero(jobs_dir):
    _run(jobs_dir, "a", size=10, mtime=1)
    _run(jobs_dir, "b", size=10, mtime=2)
    assert prune_old_runs(0, 0) == []
    assert {p.name for p in jobs_dir.iterdir()} == {"a", "b"}


def test_prune_under_cap_deletes_nothing(jobs_dir):
    _run(jobs_dir, "a", size=100, mtime=1)
    _run(jobs_dir, "b", size=100, mtime=2)
    assert prune_old_runs(keep=1, max_total_mb=10) == []        # ~200 B << 10 MB
    assert len(list(jobs_dir.iterdir())) == 2


def test_prune_deletes_oldest_until_under_cap(jobs_dir):
    for i, name in enumerate(["old", "mid", "new", "newest"]):
        _run(jobs_dir, name, size=_MB, mtime=float(i))          # newest has the largest mtime
    removed = prune_old_runs(keep=1, max_total_mb=2)            # 4 MB total, keep newest, trim to <=2 MB
    assert set(removed) == {"old", "mid"}
    assert {p.name for p in jobs_dir.iterdir()} == {"new", "newest"}


def test_prune_keeps_the_newest_and_never_touches_protected(jobs_dir):
    for i, name in enumerate(["old", "mid", "cur"]):
        _run(jobs_dir, name, size=_MB, mtime=float(i))          # cur is newest
    removed = prune_old_runs(keep=1, max_total_mb=1, protect=frozenset({"old"}))
    assert "cur" not in removed and "old" not in removed        # newest + protected survive
    assert removed == ["mid"]
    assert (jobs_dir / "old").is_dir() and (jobs_dir / "cur").is_dir()
