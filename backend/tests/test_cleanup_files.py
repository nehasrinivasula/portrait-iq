"""Item ids and job folders for the cleanup choice. No database or cluster."""
import pytest

from app.cleanup.files import CleanupError, clear_job_files, folder_stats, human_size, parse_ids, safe_job_dir
from app.config import settings


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
