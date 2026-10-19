"""
SCRUM-38: plugin contract and registry. The key acceptance check is
test_new_plugin_needs_only_a_register_line: a brand-new plugin class is
registered and then configured, run and cleaned up by the orchestrator with
no edit to any existing plugin, the orchestrator or the API.
"""
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  (registers tables on Base)
from app.auth import User
from app.collectors.base import (
    Collector,
    CollectorResult,
    PluginConfigError,
    ResourceLimits,
)
from app.collectors.registry import PluginRegistry, UnknownPluginError, get_registry
from app.collectors.tshark import TsharkCollector
from app.db import Base
from app.orchestrator import routes
from app.orchestrator.jobs import create_job, run_job
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


@pytest.fixture
def local_registry():
    CALLS.clear()
    reg = PluginRegistry()
    for cls in (Recorder, Second, Exploding):
        reg.register(cls)
    return reg


def _job(db, tools):
    return create_job(db, mode="live", tools=tools, limits=LIMITS)


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
    with pytest.raises(UnknownPluginError, match="exploding, recorder, second"):
        local_registry.get("nope")


# configure() --------------------------------------------------------------

def test_default_configure_rejects_options():
    plugin = get_registry().get("netshoot")(LIMITS)
    plugin.configure({})
    with pytest.raises(PluginConfigError, match="takes no options"):
        plugin.configure({"verbose": True})


def test_tshark_configure():
    t = TsharkCollector(LIMITS)
    t.configure({})
    assert (t.mode, t.headers_only) == ("run", True)
    t.configure({"mode": "import", "headers_only": False})
    assert (t.mode, t.headers_only) == ("import", False)
    for bad in ({"mode": "sniff"}, {"headers_only": "yes"}, {"interface": "eth0"}):
        with pytest.raises(PluginConfigError):
            t.configure(bad)


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


def test_failure_still_cleans_up_every_started_plugin(db, local_registry):
    with pytest.raises(RuntimeError):
        run_job(db, _job(db, ["recorder", "exploding", "second"]), "192.0.2.10",
                registry=local_registry)
    assert "second.run" not in CALLS
    assert CALLS[-2:] == ["exploding.cleanup", "recorder.cleanup"]


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
    ):
        with pytest.raises(HTTPException) as exc:
            routes.start_job(req, target_ip="192.0.2.10", db=db, user=user)
        assert exc.value.status_code == 400
    assert db.query(models.Job).count() == 0  # rejected before a job was created


def test_start_job_passes_options_through(db):
    user = User(id="tester", name="Tester")
    req = JobCreateRequest(tools=["tshark"], plugin_options={"tshark": {"mode": "import"}})
    job = routes.start_job(req, target_ip="192.0.2.10", db=db, user=user)
    # tshark's run() is still a stub, so the job fails, but it got past configure.
    assert job.status == "failed"
    assert json.loads(job.tools) == ["tshark"]
