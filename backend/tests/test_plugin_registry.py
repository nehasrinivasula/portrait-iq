"""
SCRUM-38: plugin contract and registry. The key acceptance check is
test_new_plugin_needs_only_a_register_line: a brand-new plugin class is
registered and then configured, run and cleaned up by the orchestrator with
no edit to any existing plugin, the orchestrator or the API.
"""
import io
import json
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, uploads  # noqa: F401  (models registers tables on Base)
from app.auth import User
from app.collectors.base import (
    Collector,
    CollectorResult,
    PluginConfigError,
    PluginError,
    ResourceLimits,
)
from app.collectors.registry import PluginRegistry, UnknownPluginError, get_registry
from app.collectors import tshark as tshark_module
from app.collectors.tshark import TsharkCollector
from app.config import settings
from app.db import Base
from app.orchestrator import routes
from app.orchestrator.jobs import create_job, read_results, run_job
from app.schemas import JobCreateRequest

LIMITS = ResourceLimits(cpu_pct=50, mem_mb=512, max_output_mb=100)
BUILTINS = {"hub_import", "netshoot", "rti", "tshark"}


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# Test plugins -------------------------------------------------------------

CALLS: list[str] = []


class Recorder(Collector):
    name = "recorder"

    def configure(self, options):
        CALLS.append(f"{self.name}.configure")
        if options.get("bad"):
            raise PluginConfigError("bad option")
        self.options = dict(options)

    def run(self, target_ip):
        CALLS.append(f"{self.name}.run")
        return CollectorResult(tool=self.name)

    def cleanup(self):
        CALLS.append(f"{self.name}.cleanup")


class Second(Recorder):
    name = "second"


class Exploding(Recorder):
    name = "exploding"

    def run(self, target_ip):
        CALLS.append(f"{self.name}.run")
        raise RuntimeError("tool crashed")


class Leaky(Recorder):
    """cleanup() fails, as if a pod could not be deleted."""
    name = "leaky"

    def cleanup(self):
        CALLS.append(f"{self.name}.cleanup")
        raise RuntimeError("pod delete timed out")


class Flaky(Recorder):
    """configure() works once, then fails: proves the job runs the
    instance start_job validated instead of configuring a second one."""
    name = "flaky"
    configured = 0

    def configure(self, options):
        Flaky.configured += 1
        if Flaky.configured > 1:
            raise PluginConfigError("configured twice")
        super().configure(options)


@pytest.fixture
def local_registry():
    CALLS.clear()
    Flaky.configured = 0
    reg = PluginRegistry()
    for cls in (Recorder, Second, Exploding, Leaky, Flaky):
        reg.register(cls)
    return reg


@pytest.fixture
def live_capture(monkeypatch):
    monkeypatch.setattr(settings, "tshark_live_capture_enabled", True)


def _job(db, tools):
    return create_job(db, mode="live", tools=tools, limits=LIMITS)


def _start(db, req, user=User(id="tester", name="Tester")):
    """start_job, then the background task it queued, on the test's session."""
    tasks = BackgroundTasks()
    job = routes.start_job(req, target_ip="192.0.2.10", background_tasks=tasks, db=db, user=user)
    assert job.status == "queued"   # returned before the job ran
    for task in tasks.tasks:
        assert task.func is routes._run_in_background
        routes.run_existing_job(db, *task.args)
    db.refresh(job)
    return job


# Registry contents ------------------------------------------------------

def test_builtin_plugins_are_registered():
    reg = get_registry()
    assert BUILTINS <= set(reg.names())
    assert reg.load_errors == {}


def test_broken_plugin_entries_are_skipped_not_fatal():
    reg = PluginRegistry()
    reg.load([
        "app.collectors.netshoot:NetshootCollector",   # fine
        "app.collectors.does_not_exist:Anything",      # module fails to import
        "app.collectors.rti:NoSuchClass",              # class missing
        "app.collectors.base:Collector",               # not a usable plugin (abstract)
        "no-colon-here",                               # malformed entry
        "app.collectors.tshark:TsharkCollector",       # fine, after the failures
    ])
    assert reg.names() == ["netshoot", "tshark"]
    assert set(reg.load_errors) == {
        "app.collectors.does_not_exist:Anything",
        "app.collectors.rti:NoSuchClass",
        "app.collectors.base:Collector",
        "no-colon-here",
    }
    assert "ModuleNotFoundError" in reg.load_errors["app.collectors.does_not_exist:Anything"]


def test_new_plugin_needs_only_a_register_line(db):
    """A plugin the orchestrator has never seen: one register() call (the
    line that would go in _register_defaults) and run_job handles it."""
    events = []

    class PingPlugin(Collector):
        name = "ping"

        def run(self, target_ip):
            events.append(("run", target_ip))
            return CollectorResult(tool=self.name)

        def cleanup(self):
            events.append(("cleanup",))

    reg = PluginRegistry()
    reg.register(PingPlugin)

    job = run_job(db, _job(db, ["ping"]), "192.0.2.10", registry=reg)
    assert job.status == "done"
    assert events == [("run", "192.0.2.10"), ("cleanup",)]


# Registration rules -------------------------------------------------------

def test_register_rejects_non_plugins():
    with pytest.raises(TypeError):
        PluginRegistry().register(object)


def test_register_rejects_missing_methods():
    class Half(Collector):
        name = "half"

        def run(self, target_ip): ...

    with pytest.raises(TypeError, match="cleanup"):
        PluginRegistry().register(Half)


@pytest.mark.parametrize("bad", ["", "Bad", "has-dash", "9lives", "x"])
def test_register_rejects_bad_names(bad):
    cls = type("Named", (Recorder,), {"name": bad})
    with pytest.raises(ValueError):
        PluginRegistry().register(cls)


def test_register_rejects_duplicate_names():
    reg = PluginRegistry()
    reg.register(Recorder)
    reg.register(Recorder)  # same class again is fine
    clash = type("Clash", (Recorder,), {"name": "recorder"})
    with pytest.raises(ValueError, match="already used"):
        reg.register(clash)


def test_unknown_plugin_lists_the_available_ones(local_registry):
    with pytest.raises(UnknownPluginError, match="exploding, flaky, leaky, recorder, second"):
        local_registry.get("nope")


# configure() --------------------------------------------------------------

def test_default_configure_rejects_options():
    plugin = get_registry().get("hub_import")(LIMITS)
    plugin.configure({})
    with pytest.raises(PluginConfigError, match="takes no options"):
        plugin.configure({"verbose": True})


def test_tshark_configure(live_capture):
    t = TsharkCollector(LIMITS)
    t.configure({})
    assert (t.mode, t.headers_only) == ("run", True)
    upload_id, _ = uploads.save(io.BytesIO(b"\x0a\x0d\x0d\x0a" + b"\0" * 28), "pcap")
    t.configure({"mode": "import", "upload_id": upload_id,
                 "headers_only": False, "full_payload_reason": "ticket 123"})
    assert (t.mode, t.headers_only) == ("import", False)
    for bad in ({"mode": "sniff"}, {"headers_only": "yes"}, {"verbose": True}):
        with pytest.raises(PluginConfigError):
            t.configure(bad)


def test_tshark_full_payload_needs_a_reason(live_capture):
    t = TsharkCollector(LIMITS)
    for missing in ({"headers_only": False}, {"headers_only": False, "full_payload_reason": "  "}):
        with pytest.raises(PluginConfigError, match="full_payload_reason"):
            t.configure(missing)
    with pytest.raises(PluginConfigError, match="only applies"):
        t.configure({"full_payload_reason": "ticket 123"})
    with pytest.raises(PluginConfigError, match="must be text"):
        t.configure({"headers_only": False, "full_payload_reason": 42})
    with pytest.raises(PluginConfigError, match="limited to 500"):
        t.configure({"headers_only": False, "full_payload_reason": "x" * 501})
    t.configure({"headers_only": False, "full_payload_reason": "x" * 500})  # the limit itself is fine

    t.configure({"headers_only": False, "full_payload_reason": " Hospital IT asked, ticket 123 "})
    assert t.audit_fields() == {
        "mode": "run", "headers_only": False, "full_payload_reason": "Hospital IT asked, ticket 123",
        "duration_s": 60, "interface": "any",
    }
    t.configure({})
    assert t.audit_fields() == {"mode": "run", "headers_only": True, "duration_s": 60, "interface": "any"}


def test_create_all_rejects_duplicates_and_stray_options(local_registry):
    with pytest.raises(PluginConfigError, match="more than once"):
        local_registry.create_all(["recorder", "recorder"], LIMITS)
    with pytest.raises(PluginConfigError, match="not selected"):
        local_registry.create_all(["recorder"], LIMITS, {"secnod": {}})


# Orchestrator lifecycle ---------------------------------------------------

def test_all_plugins_configured_before_any_runs(db, local_registry):
    job = run_job(
        db, _job(db, ["recorder", "second"]), "192.0.2.10",
        plugin_options={"second": {"bad": True}}, registry=local_registry,
    )
    assert job.status == "failed"
    assert CALLS == ["recorder.configure", "second.configure"]  # nothing ran


def test_happy_path_order(db, local_registry):
    job = run_job(db, _job(db, ["recorder", "second"]), "192.0.2.10", registry=local_registry)
    assert job.status == "done"
    assert CALLS == [
        "recorder.configure", "second.configure",
        "recorder.run", "second.run",
        "second.cleanup", "recorder.cleanup",
    ]


def test_one_tool_crashing_does_not_stop_the_others(db, local_registry):
    job = run_job(db, _job(db, ["recorder", "exploding", "second"]), "192.0.2.10",
                  registry=local_registry)
    assert job.status == "partial"
    assert "second.run" in CALLS                      # ran after the crash
    assert CALLS[-3:] == ["second.cleanup", "exploding.cleanup", "recorder.cleanup"]


def test_known_tool_failure_continues_and_is_shown_to_the_user(db, local_registry):
    class Refusing(Recorder):
        name = "refusing"

        def run(self, target_ip):
            raise PluginError("cluster unreachable")

    local_registry.register(Refusing)
    job = run_job(db, _job(db, ["refusing", "second"]), "192.0.2.10", registry=local_registry)
    assert job.status == "partial"
    results = read_results(job.id)
    assert results[0] == {"tool": "refusing", "error": "cluster unreachable"}   # safe message kept
    assert results[1]["tool"] == "second" and "error" not in results[1]


def test_job_fails_only_when_every_tool_failed(db, local_registry):
    job = run_job(db, _job(db, ["exploding"]), "192.0.2.10", registry=local_registry)
    assert job.status == "failed"
    assert read_results(job.id) == [{"tool": "exploding", "error": "RuntimeError"}]
    assert CALLS[-1] == "exploding.cleanup"


def test_failed_cleanup_is_recorded_and_the_rest_still_run(db, local_registry, caplog):
    job = run_job(db, _job(db, ["recorder", "leaky", "second"]), "192.0.2.10",
                  registry=local_registry)
    assert job.status == "cleanup_failed"
    assert CALLS[-3:] == ["second.cleanup", "leaky.cleanup", "recorder.cleanup"]
    assert f"job {job.id}: cleanup of plugin leaky failed" in caplog.text
    audit = db.query(models.AuditLogEntry).filter_by(action="plugin_cleanup_failed").one()
    assert audit.detail == f"job={job.id} plugin=leaky error=RuntimeError"


def test_failed_cleanup_after_a_failed_run_is_still_reported(db, local_registry):
    run_job(db, _job(db, ["recorder", "leaky", "exploding"]), "192.0.2.10",
            registry=local_registry)
    job = db.query(models.Job).one()
    assert job.status == "cleanup_failed"   # outranks the tool's own failure
    assert CALLS[-3:] == ["exploding.cleanup", "leaky.cleanup", "recorder.cleanup"]


def test_unknown_plugin_fails_the_job(db, local_registry):
    job = run_job(db, _job(db, ["nope"]), "192.0.2.10", registry=local_registry)
    assert job.status == "failed"


# API ----------------------------------------------------------------------

def test_job_request_rejects_unknown_fields():
    # The old tshark_mode field must fail loudly, not be silently ignored.
    with pytest.raises(ValidationError, match="tshark_mode"):
        JobCreateRequest(tools=["tshark"], tshark_mode="import")


def test_list_plugins_endpoint():
    out = routes.list_plugins()
    assert out.load_errors == {}
    assert BUILTINS <= {p.name for p in out.plugins}
    tshark = next(p for p in out.plugins if p.name == "tshark")
    assert tshark.display_name == "Wireshark / tshark"


def test_start_job_rejects_bad_selection_with_400(db):
    user = User(id="tester", name="Tester")
    for req in (
        JobCreateRequest(tools=["nope"]),
        JobCreateRequest(tools=["tshark"], plugin_options={"tshark": {"mode": "sniff"}}),
        JobCreateRequest(tools=["tshark"], plugin_options={"tshark": {"mode": "import"}}),
        JobCreateRequest(tools=["tshark"]),   # live capture is off by default
    ):
        with pytest.raises(HTTPException) as exc:
            routes.start_job(req, target_ip="192.0.2.10", background_tasks=BackgroundTasks(),
                             db=db, user=user)
        assert exc.value.status_code == 400
    assert db.query(models.Job).count() == 0  # rejected before a job was created


def test_start_job_passes_options_through(db, monkeypatch):
    calls = []
    monkeypatch.setattr(tshark_module, "_wireshark_tool",
                        lambda name, args, timeout_s: calls.append([name, *args]) or "eth frames:7 bytes:700")
    upload_id, _ = uploads.save(io.BytesIO(b"\xd4\xc3\xb2\xa1" + b"\0" * 20), "pcap")
    req = JobCreateRequest(tools=["tshark"], plugin_options={"tshark": {"mode": "import", "upload_id": upload_id}})
    job = _start(db, req)
    assert job.status == "done"
    assert json.loads(job.tools) == ["tshark"]
    assert calls[0][:4] == ["editcap", "-F", "pcapng", "-s"]
    assert read_results(job.id)[0]["summary"] == "7 packets imported, headers only"


def test_start_job_audits_full_payload_and_reason(db, live_capture):
    user = User(id="tester", name="Tester")
    req = JobCreateRequest(tools=["tshark"], plugin_options={
        "tshark": {"headers_only": False, "full_payload_reason": "ticket 123"},
    })
    job = routes.start_job(req, target_ip="192.0.2.10", background_tasks=BackgroundTasks(),
                           db=db, user=user)
    detail = db.query(models.AuditLogEntry).filter_by(action="job_start").one().detail
    assert f"job={job.id}" in detail
    assert "'headers_only': False" in detail and "'full_payload_reason': 'ticket 123'" in detail


def test_start_job_runs_the_instances_it_validated(db, local_registry, monkeypatch):
    monkeypatch.setattr(routes, "get_registry", lambda: local_registry)
    job = _start(db, JobCreateRequest(tools=["flaky"]))
    assert job.status == "done"
    assert Flaky.configured == 1


def test_results_are_kept_for_plugins_that_finished(db, local_registry):
    run_job(db, _job(db, ["recorder", "exploding"]), "192.0.2.10", registry=local_registry)
    job = db.query(models.Job).one()
    results = read_results(job.id)
    assert [r["tool"] for r in results] == ["recorder", "exploding"]
    assert results[0]["warnings"] == [] and "error" not in results[0]
    assert results[1] == {"tool": "exploding", "error": "RuntimeError"}   # type only, not the text


def test_plugins_get_their_own_job_folder(db, local_registry):
    job = run_job(db, _job(db, ["recorder", "second"]), "192.0.2.10", registry=local_registry)
    assert job.status == "done"
    for name in ("recorder", "second"):
        assert (Path(settings.job_output_dir) / job.id / name).is_dir()


def test_startup_marks_interrupted_jobs(db):
    from app.orchestrator.jobs import recover_interrupted_jobs
    running = _job(db, ["recorder"])
    running.status = "running"
    finished = _job(db, ["recorder"])
    finished.status = "done"
    db.commit()
    recover_interrupted_jobs(db)
    assert (running.status, finished.status) == ("interrupted", "done")
    assert db.query(models.AuditLogEntry).filter_by(action="job_interrupted").count() == 1
