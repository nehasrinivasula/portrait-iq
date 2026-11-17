"""Cleanup choice after a crash and at sign-out: what is listed, what remove and
keep do, what is audited, and that nothing outside the list can be deleted."""
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.cleanup import service  # noqa: E402
from app.cleanup.files import CleanupError  # noqa: E402
from app.collectors import k8s  # noqa: E402
from app.collectors.base import PluginError  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import AuditLogEntry, CleanupDecision, Job  # noqa: E402
from app.orchestrator.jobs import recover_interrupted_jobs  # noqa: E402


class FakeRunner:
    def __init__(self, pods=()):
        self.pods = {p.name: p for p in pods}
        self.deleted, self.fail = [], set()

    def list_tool_pods(self):
        return list(self.pods.values())

    def delete(self, name):
        if name in self.fail:
            raise PluginError("could not delete pod", safe_reason="Could not remove the diagnostic's pod.")
        self.deleted.append(name)
        self.pods.pop(name, None)

    def sweep(self):
        names = list(self.pods)
        for n in names:
            self.delete(n)
        return names


def pod(name, job_id, tool="tshark", phase="Running"):
    return k8s.PodInfo(name=name, tool=tool, job_id=job_id, phase=phase, created_at=None)


@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    path = tmp_path / "jobs"
    path.mkdir()
    monkeypatch.setattr(settings, "job_output_dir", str(path))
    return path


def add_job(db, status, files=0, job_id=None):
    job = Job(status=status, mode="live", tools="[]", requested_by="neha")
    if job_id:
        job.id = job_id
    db.add(job)
    db.commit()
    return job


def add_files(jobs_dir, job, n=2):
    folder = jobs_dir / job.id / "tshark"
    folder.mkdir(parents=True)
    for i in range(n):
        (folder / f"f{i}.pcap").write_bytes(b"x" * 100)


def use_runner(monkeypatch, runner):
    monkeypatch.setattr(k8s, "get_pod_runner", lambda: runner)
    return runner


def audit(db, action):
    return [e for e in db.query(AuditLogEntry).filter_by(action=action).all()]


# What is listed ---------------------------------------------------------------

def test_a_clean_system_reports_clean(db, jobs_dir, monkeypatch):
    use_runner(monkeypatch, FakeRunner())
    status = service.current_status(db)
    assert status.state == "clean" and status.items == []


def test_leftovers_are_grouped_per_run(db, jobs_dir, monkeypatch):
    dead = add_job(db, "interrupted")
    add_files(jobs_dir, dead)
    use_runner(monkeypatch, FakeRunner([pod("piq-tshark-aaaa", dead.id), pod("piq-rti-bbbb", "adhoc", "rti")]))
    status = service.current_status(db)
    assert status.state == "pending" and status.interrupted_runs == 1
    groups = {g.job_id: g for g in status.groups}
    assert [i.kind for i in groups[dead.id].items] == ["pod", "files"]
    assert groups[dead.id].status == "interrupted"
    assert groups["adhoc"].status is None and groups["adhoc"].items[0].label == "piq-rti-bbbb"
    assert "2 files, 200 B" in groups[dead.id].items[1].detail


def test_files_of_a_finished_run_are_results_not_leftovers(db, jobs_dir, monkeypatch):
    done = add_job(db, "done")
    add_files(jobs_dir, done)
    use_runner(monkeypatch, FakeRunner())
    assert service.current_status(db).items == []


def test_an_empty_job_folder_is_not_listed(db, jobs_dir, monkeypatch):
    dead = add_job(db, "interrupted")
    (jobs_dir / dead.id).mkdir()
    use_runner(monkeypatch, FakeRunner())
    assert service.current_status(db).state == "clean"


def test_an_unreachable_cluster_is_reported_not_called_clean(db, jobs_dir, monkeypatch):
    def boom():
        raise PluginError("No Kubernetes access")
    monkeypatch.setattr(k8s, "get_pod_runner", boom)
    status = service.current_status(db)
    assert status.state == "unchecked" and status.pods_error


def test_pods_of_a_running_job_are_active_and_the_state_is_busy(db, jobs_dir, monkeypatch):
    running = add_job(db, "running")
    use_runner(monkeypatch, FakeRunner([pod("piq-tshark-live", running.id)]))
    status = service.current_status(db)
    assert status.state == "busy" and status.items[0].active and status.pending == []


# Remove and keep ----------------------------------------------------------------

def test_remove_deletes_the_pod_and_the_files_and_the_state_is_clean(db, jobs_dir, monkeypatch):
    dead = add_job(db, "interrupted")
    add_files(jobs_dir, dead)
    runner = use_runner(monkeypatch, FakeRunner([pod("piq-tshark-aaaa", dead.id)]))
    outcomes, after = service.apply_choice(
        db, actor="neha", session_id="s1", action="remove",
        ids=["pod:piq-tshark-aaaa", f"files:{dead.id}"], context="restart")
    assert [o.result for o in outcomes] == ["removed", "removed"]
    assert runner.deleted == ["piq-tshark-aaaa"]
    assert not (jobs_dir / dead.id).exists()
    assert after.state == "clean"


def test_keep_records_the_choice_and_stops_the_prompt(db, jobs_dir, monkeypatch):
    use_runner(monkeypatch, FakeRunner([pod("piq-tshark-aaaa", "adhoc")]))
    outcomes, after = service.apply_choice(
        db, actor="neha", session_id="s1", action="keep", ids=["pod:piq-tshark-aaaa"], context="logout")
    assert outcomes[0].result == "kept"
    assert after.state == "preserved" and after.pending == []
    assert db.get(CleanupDecision, "pod:piq-tshark-aaaa").decided_by == "neha"
    assert service.current_status(db).items[0].kept


def test_removing_a_kept_item_later_clears_the_decision(db, jobs_dir, monkeypatch):
    use_runner(monkeypatch, FakeRunner([pod("piq-tshark-aaaa", "adhoc")]))
    service.apply_choice(db, actor="n", session_id=None, action="keep", ids=["pod:piq-tshark-aaaa"], context="manual")
    service.apply_choice(db, actor="n", session_id=None, action="remove", ids=["pod:piq-tshark-aaaa"], context="manual")
    assert db.get(CleanupDecision, "pod:piq-tshark-aaaa") is None
    assert service.current_status(db).state == "clean"


def test_a_pod_that_cannot_be_deleted_is_reported_and_stays_pending(db, jobs_dir, monkeypatch):
    runner = use_runner(monkeypatch, FakeRunner([pod("piq-a", "adhoc"), pod("piq-b", "adhoc")]))
    runner.fail.add("piq-a")
    outcomes, after = service.apply_choice(
        db, actor="n", session_id=None, action="remove", ids=["pod:piq-a", "pod:piq-b"], context="logout")
    assert {o.id: o.result for o in outcomes} == {"pod:piq-a": "failed", "pod:piq-b": "removed"}
    assert outcomes[0].reason == "Could not remove the diagnostic's pod."
    assert after.state == "pending" and [i.label for i in after.pending] == ["piq-a"]


def test_a_running_jobs_pod_is_never_removed(db, jobs_dir, monkeypatch):
    running = add_job(db, "running")
    runner = use_runner(monkeypatch, FakeRunner([pod("piq-live", running.id)]))
    outcomes, _ = service.apply_choice(
        db, actor="n", session_id=None, action="remove", ids=["pod:piq-live"], context="logout")
    assert outcomes[0].result == "skipped" and runner.deleted == []


def test_ids_that_are_not_listed_delete_nothing(db, jobs_dir, monkeypatch):
    done = add_job(db, "done")
    add_files(jobs_dir, done)
    runner = use_runner(monkeypatch, FakeRunner([pod("piq-real", "adhoc")]))
    outcomes, _ = service.apply_choice(
        db, actor="n", session_id=None, action="remove",
        ids=["pod:piq-invented", f"files:{done.id}", "files:no-such-job"], context="manual")
    assert [o.result for o in outcomes] == ["skipped"] * 3
    assert runner.deleted == [] and (jobs_dir / done.id).exists()


@pytest.mark.parametrize("bad", [[], ["files:../x"], ["pod:a b"], ["nonsense"]])
def test_malformed_requests_are_refused(db, jobs_dir, monkeypatch, bad):
    use_runner(monkeypatch, FakeRunner())
    with pytest.raises(CleanupError):
        service.apply_choice(db, actor="n", session_id=None, action="remove", ids=bad, context="manual")


# Audit --------------------------------------------------------------------------

def test_every_choice_is_audited_with_what_happened_and_the_final_state(db, jobs_dir, monkeypatch):
    use_runner(monkeypatch, FakeRunner([pod("piq-a", "adhoc"), pod("piq-b", "adhoc")]))
    service.apply_choice(db, actor="neha", session_id="sess-9", action="remove", ids=["pod:piq-a"], context="logout")
    service.apply_choice(db, actor="neha", session_id="sess-9", action="keep", ids=["pod:piq-b"], context="logout")
    first, second = audit(db, "cleanup_choice")
    assert first.actor == "neha" and first.session_id == "sess-9"
    assert "context=logout action=remove removed=pod:piq-a" in first.detail and "state=pending" in first.detail
    assert "action=keep" in second.detail and "kept=pod:piq-b" in second.detail and "state=preserved" in second.detail


# Crash and restart ----------------------------------------------------------------

def test_after_a_crash_the_pods_are_kept_until_the_engineer_chooses(db, jobs_dir, monkeypatch):
    monkeypatch.setattr(settings, "sweep_tool_pods_on_startup", False)
    job = add_job(db, "running")
    add_files(jobs_dir, job)
    runner = use_runner(monkeypatch, FakeRunner([pod("piq-tshark-aaaa", job.id)]))

    recover_interrupted_jobs(db)                         # the restart

    db.refresh(job)
    assert job.status == "interrupted"
    assert runner.deleted == [] and "piq-tshark-aaaa" in runner.pods
    assert audit(db, "job_interrupted") and "pods=1" in audit(db, "leftovers_pending")[0].detail
    status = service.current_status(db)
    assert status.state == "pending" and status.interrupted_runs == 1

    outcomes, after = service.apply_choice(              # the engineer's choice
        db, actor="neha", session_id="s2", action="remove",
        ids=[i.id for i in status.pending], context="restart")
    assert [o.result for o in outcomes] == ["removed", "removed"]
    assert runner.pods == {} and not (jobs_dir / job.id).exists()
    assert after.state == "clean"
    assert "context=restart" in audit(db, "cleanup_choice")[0].detail


def test_the_automatic_sweep_is_still_available_when_switched_on(db, jobs_dir, monkeypatch):
    monkeypatch.setattr(settings, "sweep_tool_pods_on_startup", True)
    job = add_job(db, "running")
    runner = use_runner(monkeypatch, FakeRunner([pod("piq-x", job.id)]))
    recover_interrupted_jobs(db)
    assert runner.deleted == ["piq-x"] and audit(db, "tool_pods_swept")
    assert not audit(db, "leftovers_pending")
