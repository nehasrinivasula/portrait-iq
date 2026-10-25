"""The completeness verdict for a collection. No database needed."""
from app.storage.completeness import FileLedger, Issue, ManifestEntry, assess, is_complete


def ledger(name="a.csv", read=10, expected=10, rejected=0, in_store=10, parser="portrait_csv"):
    return FileLedger(name, parser, read, expected, rejected, in_store)


def run(**kw):
    args = dict(job_status="done", files=[ledger()], manifest=[], events_without_record=0)
    args.update(kw)
    return assess(**args)


def kinds(issues):
    return sorted(i.kind for i in issues)


def test_clean_collection_is_complete():
    issues = run()
    assert issues == [] and is_complete(issues)


def test_collection_with_no_job_status_can_still_be_complete():
    assert is_complete(run(job_status=None))   # the "unassigned" bucket has no job


def test_unfinished_or_failed_collection_is_not_complete():
    assert kinds(run(job_status="running")) == ["collection_running"]
    assert kinds(run(job_status="queued")) == ["collection_running"]
    assert kinds(run(job_status="failed")) == ["collection_failed"]
    assert kinds(run(job_status="cleanup_failed")) == ["collection_failed"]


def test_nothing_stored_is_a_problem_not_an_empty_success():
    issues = run(files=[])
    assert kinds(issues) == ["no_logs"] and not is_complete(issues)


def test_events_deleted_after_ingest_are_detected():
    issues = run(files=[ledger(expected=10, in_store=7)])
    (issue,) = issues
    assert issue.kind == "store_mismatch" and issue.file == "a.csv"
    assert "10" in issue.message and "7" in issue.message


def test_extra_events_in_store_are_also_a_mismatch():
    assert kinds(run(files=[ledger(expected=10, in_store=12)])) == ["store_mismatch"]


def test_rejected_rows_are_reported_with_their_count():
    issues = run(files=[ledger(read=101, expected=100, rejected=1, in_store=100)])
    (issue,) = issues
    assert issue.kind == "rows_rejected" and "1 of 101" in issue.message and not is_complete(issues)


def test_row_accounting_gap_is_reported():
    issues = run(files=[ledger(read=10, expected=8, rejected=1, in_store=8)])
    assert "unaccounted_rows" in kinds(issues)


def test_events_without_a_receipt_cannot_be_confirmed():
    issues = run(files=[], events_without_record=5)
    assert kinds(issues) == ["untracked_events"] and "5" in issues[0].message


def test_files_that_never_made_it_in_are_listed_by_name():
    manifest = [
        ManifestEntry("good.csv", "ingested"),
        ManifestEntry("bad.csv", "failed", "3 of 10 rows could not be converted"),
        ManifestEntry("odd.log", "unrecognized"),
        ManifestEntry("big.zip", "over_budget"),
        ManifestEntry("raw.csv", "acquired"),
    ]
    issues = run(manifest=manifest)
    assert sorted(i.file for i in issues) == sorted(["bad.csv", "big.zip", "odd.log", "raw.csv"])
    assert all(i.kind == "file_not_stored" for i in issues)
    assert "3 of 10 rows" in next(i for i in issues if i.file == "bad.csv").message
    assert not is_complete(issues)


def test_duplicate_file_is_information_not_a_problem():
    issues = run(manifest=[ManifestEntry("copy.csv", "duplicate")])
    assert kinds(issues) == ["duplicate_file"] and is_complete(issues)


def test_every_problem_is_reported_not_just_the_first():
    issues = run(job_status="running", files=[ledger(expected=10, in_store=9)],
                 manifest=[ManifestEntry("x.csv", "failed")], events_without_record=2)
    assert kinds(issues) == ["collection_running", "file_not_stored", "store_mismatch", "untracked_events"]
