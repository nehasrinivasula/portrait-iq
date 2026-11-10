"""Export a run as a zip (SCRUM-61): preview, step-by-step progress, masking,
the fixed layout, errors that explain themselves, and the audit trail.
Same setup as test_privacy_routes.py: real routes, in-memory database."""
import io
import json
import zipfile

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import User, get_current_user
from app.db import Base, get_db
from app.ingestion import pipeline
from app.main import app
from app.models import AuditLogEntry, Job, Signal
from app.orchestrator.jobs import job_output_dir
from app.packaging import service

HEADER = (
    "timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,"
    "sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description"
)
SSN = "123-45-6789"
LAYOUT = {"metadata.json", "logs/", "logs/events.jsonl", "logs/summary.json", "privacy/", "privacy/report.json",
          "tools/", "tools/results.json", "findings/", "findings/findings.json", "audit/", "audit/audit.jsonl"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(id="tester", name="Tester")
    monkeypatch.setattr("app.auth.is_locked", lambda *_: False)
    monkeypatch.setattr("app.packaging.routes.SessionLocal", Session)   # the background build's own session
    seed = Session()
    seed.add(Job(id="job-1", mode="live", status="done", tools='["netshoot", "tshark"]'))
    seed.add(Signal(job_id="job-1", rule_id="r1", title="Timeouts", severity="warning", count=2))
    seed.commit()
    lines = [HEADER] + [
        f'"2024-06-02T04:00:{i:02d}.000+0000","0","{i}","INFO","host-a","u","unsetFriendlyName","svc[1:0]",":0","General","{m}"'
        for i, m in enumerate(["all quiet", f"billing ssn {SSN}"])
    ]
    path = tmp_path / "a.csv"
    path.write_text("\n".join(lines) + "\n")
    pipeline.ingest_file(path, seed, job_id="job-1")

    out = job_output_dir("job-1")
    (out / "netshoot").mkdir(parents=True)
    (out / "netshoot" / "checks.txt").write_text(f"ping ok\nnote: ssn {SSN}\n")
    (out / "tshark").mkdir()
    (out / "tshark" / "capture.pcap").write_bytes(b"\xd4\xc3\xb2\xa1binary")
    (out / "results.json").write_text(json.dumps([
        {"tool": "netshoot", "summary": "2 checks", "warnings": [],
         "artifact_paths": [str(out / "netshoot" / "checks.txt"), str(out / "netshoot" / "gone.txt")]},
        {"tool": "tshark", "summary": "1 capture", "warnings": [], "artifact_paths": [str(out / "tshark" / "capture.pcap")]},
        {"tool": "rti", "error": "RTI is turned off on this server."},
    ]))
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()
    service._building.clear()


def export(client, **body):
    r = client.post("/api/jobs/job-1/package", json=body)
    assert r.status_code == 202, r.text
    r = client.get("/api/jobs/job-1/package")
    assert r.status_code == 200, r.text
    return r.json()


def open_zip(client):
    r = client.get("/api/jobs/job-1/package/download")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(r.content))


def test_preview_lists_what_will_be_exported(env):
    client, _ = env
    r = client.get("/api/jobs/job-1/package/preview")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["finished"] and p["events"] == 2 and p["findings"] == 1 and p["package"] is None
    assert [(t["tool"], t["ok"], t["files"]) for t in p["tools"]] == [("netshoot", True, 2), ("tshark", True, 1), ("rti", False, 0)]
    assert p["tools"][2]["error"] == "RTI is turned off on this server."
    assert p["privacy"]["flagged_events"] == 1
    assert [s["status"] for s in p["steps"]] == ["pending"] * len(service.STEPS)


def test_export_builds_the_standard_layout(env):
    client, _ = env
    s = export(client)
    assert s["state"] == "ready" and s["downloadable"] and s["masked"]
    assert s["file_name"].startswith("portrait-iq-job-1-") and s["size_bytes"] > 0 and len(s["sha256"]) == 64
    zf = open_zip(client)
    names = set(zf.namelist())
    assert LAYOUT <= names
    assert "tools/netshoot/checks.txt" in names
    assert "tools/tshark/capture.pcap" not in names          # binary: cannot be masked, so left out
    meta = json.loads(zf.read("metadata.json"))
    assert meta["format_version"] == 1 and meta["run"]["id"] == "job-1" and meta["export"]["masked"] is True
    assert json.loads(zf.read("findings/findings.json"))[0]["title"] == "Timeouts"


def test_masked_by_default_in_logs_and_text_tool_files(env):
    client, _ = env
    export(client)
    zf = open_zip(client)
    logs = zf.read("logs/events.jsonl").decode()
    assert SSN not in logs and "[REDACTED:ssn]" in logs
    assert SSN not in zf.read("tools/netshoot/checks.txt").decode()


def test_masked_tool_files_keep_ip_and_mac_addresses(env):
    """The Logs page masks personal data only; equipment addresses are what tool files are for."""
    client, _ = env
    (job_output_dir("job-1") / "netshoot" / "checks.txt").write_text(
        f"ping 10.1.2.3 ok\nmac aa:bb:cc:dd:ee:ff\nnote: ssn {SSN}\n")
    export(client)
    text = open_zip(client).read("tools/netshoot/checks.txt").decode()
    assert "10.1.2.3" in text and "aa:bb:cc:dd:ee:ff" in text and SSN not in text


def test_unmasked_export_includes_binary_tool_files(env):
    client, _ = env
    export(client, mask=False)
    assert "tools/tshark/capture.pcap" in open_zip(client).namelist()


def test_findings_in_the_zip_carry_their_evidence_fields(env):
    client, _ = env
    export(client)
    f = json.loads(open_zip(client).read("findings/findings.json"))[0]
    assert {"first_seen", "last_seen", "devices", "devices_total", "evidence"} <= set(f)


def test_a_failed_re_export_keeps_the_last_good_zip(env, monkeypatch):
    client, _ = env
    export(client)
    first = next(service.package_dir("job-1").glob("*.zip")).read_bytes()
    monkeypatch.setitem(service._STEP_FUNCS, "tools", lambda *_: None)
    monkeypatch.setattr(service.zipfile, "ZipFile", lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space")))
    r = client.post("/api/jobs/job-1/package", json={})
    assert r.status_code == 202, r.text
    assert client.get("/api/jobs/job-1/package").json()["state"] == "failed"
    assert [p.read_bytes() for p in service.package_dir("job-1").glob("*.zip")] == [first]


def test_unmasked_export_keeps_values_and_warns(env):
    client, _ = env
    s = export(client, mask=False)
    assert SSN in open_zip(client).read("logs/events.jsonl").decode()
    logs = next(x for x in s["steps"] if x["id"] == "logs")
    assert logs["status"] == "warn" and "not masked" in logs["detail"]
    assert any("Masking was turned off" in w for w in s["warnings"])


def test_each_step_explains_what_it_did_and_what_failed(env):
    client, _ = env
    s = export(client)
    steps = {x["id"]: x for x in s["steps"]}
    assert [x["id"] for x in s["steps"]] == [i for i, _ in service.STEPS]
    assert all(x["detail"] for x in s["steps"])
    tools = steps["tools"]
    assert tools["status"] == "warn" and tools["detail"] == "2 of 3 diagnostics ran; 1 file added."
    reasons = {i["item"]: i["reason"] for i in tools["issues"]}
    assert reasons["rti"] == "This diagnostic did not finish during the run: RTI is turned off on this server."
    assert "no longer on the server" in reasons["netshoot: gone.txt"]
    assert "Left out: only text files can be masked" in reasons["tshark: capture.pcap"]
    assert any("capture.pcap was left out" in w for w in s["warnings"])
    assert not s["partial"]


def test_a_failed_step_still_exports_the_rest(env, monkeypatch):
    client, _ = env
    monkeypatch.setitem(service._STEP_FUNCS, "findings", lambda *_: (_ for _ in ()).throw(KeyError("x")))
    s = export(client)
    assert s["state"] == "ready" and s["partial"]
    f = next(x for x in s["steps"] if x["id"] == "findings")
    assert f["status"] == "failed" and f["issues"][0]["reason"].startswith("Unexpected error (KeyError)")
    assert "logs/events.jsonl" in open_zip(client).namelist()


def test_zip_failure_fails_the_export_with_a_reason(env, monkeypatch):
    client, _ = env

    def broken(*_):
        raise service.PackageError("The zip could not be written (No space left on device).")
    monkeypatch.setattr(service, "_write_zip", broken)
    s = export(client)
    assert s["state"] == "failed" and not s["downloadable"]
    assert "No space left" in s["error"]
    assert client.get("/api/jobs/job-1/package/download").status_code == 404


def test_incomplete_scan_warns_but_allows_export(env):
    client, seed = env
    from app.models import NormalizedEvent
    for e in seed.scalars(select(NormalizedEvent)):
        e.privacy_scan_version = 0
    seed.commit()
    p = client.get("/api/jobs/job-1/package/preview").json()
    assert not p["privacy"]["complete_for_packaging"] and p["warnings"]
    s = export(client)
    assert s["state"] == "ready"
    check = next(x for x in s["steps"] if x["id"] == "check")
    assert check["status"] == "warn" and check["issues"]
    assert SSN not in open_zip(client).read("logs/events.jsonl").decode()   # masking still scans unscanned rows


def test_running_job_cannot_be_exported(env):
    client, seed = env
    seed.get(Job, "job-1").status = "running"
    seed.commit()
    r = client.post("/api/jobs/job-1/package", json={})
    assert r.status_code == 409 and "still going" in r.json()["detail"]


def test_second_export_while_preparing_is_refused(env):
    client, _ = env
    service._building.add("job-1")
    r = client.post("/api/jobs/job-1/package", json={})
    assert r.status_code == 409 and "already being prepared" in r.json()["detail"]


def test_export_cut_off_by_a_restart_reads_as_interrupted(env):
    client, seed = env
    service.start(seed, "job-1", masked=True, requested_by="tester")
    service._building.clear()                  # the process that was building it is gone
    s = client.get("/api/jobs/job-1/package").json()
    assert s["state"] == "interrupted" and "restarted" in s["error"]


def test_unknown_run_and_no_export_yet(env):
    client, _ = env
    assert client.get("/api/jobs/nope/package/preview").status_code == 404
    r = client.get("/api/jobs/job-1/package")
    assert r.status_code == 404 and "not been exported" in r.json()["detail"]


def test_new_export_replaces_the_last_zip(env):
    client, _ = env
    first = export(client)
    pkg = service.package_dir("job-1")
    (pkg / first["file_name"]).rename(pkg / "portrait-iq-job-1-old.zip")
    export(client)
    assert [p.name for p in pkg.glob("*.zip")] != ["portrait-iq-job-1-old.zip"]
    assert len(list(pkg.glob("*.zip"))) == 1


def test_export_is_audited_without_log_values(env):
    client, seed = env
    export(client)
    open_zip(client)
    rows = seed.scalars(select(AuditLogEntry).where(AuditLogEntry.action.like("package_%"))).all()
    assert [r.action for r in rows] == ["package_create", "package_ready", "package_download"]
    assert all(SSN not in (r.detail or "") for r in rows)
    assert "sha256=" in rows[-1].detail


# --- exports without a run: imported logs and all logs -------------------------------

def _import_unassigned(seed, tmp_path):
    lines = [HEADER, f'"2024-06-03T04:00:00.000+0000","0","1","INFO","host-z","u","unsetFriendlyName","svc[1:0]",":0","General","imported ssn {SSN}"']
    path = tmp_path / "imported.csv"
    path.write_text("\n".join(lines) + "\n")
    pipeline.ingest_file(path, seed, job_id=None)


def test_imported_logs_export_without_a_run(env, tmp_path):
    client, seed = env
    _import_unassigned(seed, tmp_path)
    p = client.get("/api/jobs/unassigned/package/preview").json()
    assert (p["scope"], p["title"], p["job_status"], p["finished"]) == ("imported", "Imported logs", None, True)
    assert p["events"] == 1 and p["tools"] == [] and p["findings"] == 0
    assert client.post("/api/jobs/unassigned/package", json={}).status_code == 202
    s = client.get("/api/jobs/unassigned/package").json()
    assert s["state"] == "ready" and s["file_name"].startswith("portrait-iq-imported-logs-")
    zf = zipfile.ZipFile(io.BytesIO(client.get("/api/jobs/unassigned/package/download").content))
    assert LAYOUT <= set(zf.namelist())
    logs = zf.read("logs/events.jsonl").decode().splitlines()
    assert len(logs) == 1 and SSN not in logs[0]                       # only the imported entry, masked
    meta = json.loads(zf.read("metadata.json"))
    assert meta["scope"] == "imported" and meta["run"] is None
    tools = next(x for x in s["steps"] if x["id"] == "tools")
    assert tools["status"] == "done" and "no diagnostic results" in tools["detail"]


def test_all_logs_export_covers_every_set(env, tmp_path):
    client, seed = env
    _import_unassigned(seed, tmp_path)
    p = client.get("/api/jobs/all/package/preview").json()
    assert p["scope"] == "all" and p["events"] == 3 and p["findings"] == 1
    client.post("/api/jobs/all/package", json={})
    s = client.get("/api/jobs/all/package").json()
    assert s["state"] == "ready" and s["file_name"].startswith("portrait-iq-all-logs-")
    zf = zipfile.ZipFile(io.BytesIO(client.get("/api/jobs/all/package/download").content))
    assert len(zf.read("logs/events.jsonl").decode().splitlines()) == 3
    summary = json.loads(zf.read("logs/summary.json"))
    assert {x["id"] for x in summary["sets"]} == {"job-1", "unassigned"}
    assert json.loads(zf.read("findings/findings.json"))[0]["run"] == "job-1"


def test_nothing_to_export_without_logs(env):
    client, _ = env
    r = client.post("/api/jobs/unassigned/package", json={})
    assert r.status_code == 409 and "no imported logs" in r.json()["detail"]


def test_collections_list_the_tools_each_run_used(env, tmp_path):
    client, seed = env
    _import_unassigned(seed, tmp_path)
    rows = {c["job_id"]: c for c in client.get("/api/logs/collections").json()}
    assert rows["job-1"]["tools"] == ["netshoot", "tshark"] and rows["unassigned"]["tools"] == []
