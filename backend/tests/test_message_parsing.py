"""
Free-text message parsing (ingestion/messages.py) and request/reply sequences
(storage/sequences.py, /api/logs/sequences). Messages below are synthetic,
shaped like the real sample's request/reply/publish lines; ids are made up.
"""
import csv
import io
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import User, get_current_user
from app.db import Base, get_db
from app.ingestion import pipeline
from app.ingestion.messages import classify, component_from_source_info
from app.ingestion.parsers import portrait_csv
from app.main import app
from app.models import AuditLogEntry
from app.models import NormalizedEvent as EventRow
from app.storage.sequences import build_sequences

REQ = "11111111-2222-3333-4444-555555555555"
REQ2 = "66666666-7777-8888-9999-aaaaaaaaaaaa"
PATIENT = "deadbeef-0000-1111-2222-333344445555"
CFG = "com.ge.hc.lcs.axone.configservice.message.ConfigMessageProcessor:478"
HC = "com.ge.hc.lcs.hl7outbound.acquisition.healthcheck.HealthCheckRequestCallback:61"


# --- component -----------------------------------------------------------------------

@pytest.mark.parametrize("source_info,expected", [
    (CFG, "ConfigMessageProcessor"),
    ("com.ge.x.Requester$RespDispatchRunner:471", "Requester$RespDispatchRunner"),
    ("DdsLogProcess.cpp:120", "DdsLogProcess"),            # C++ services log a source file
    ("StartupService.java:88", "StartupService"),          # log export API format
    (":0", None), ("", None), (None, None),
])
def test_component_from_source_info(source_info, expected):
    assert component_from_source_info(source_info) == expected


# --- classify: one example per observed shape ------------------------------------------

@pytest.mark.parametrize("component,message,expected", [
    ("ConfigMessageProcessor",
     f"RECEIVED: Request - uuidRequest: {REQ}, ChangeConfigCmd, uuidRequester:{REQ2}, message:...",
     ("request", "ChangeConfigCmd", REQ, None)),
    ("ConfigMessageProcessor",
     f"SENDING: Reply -  uuidRequest: {REQ}, ChangeConfigCmd, result:   status(Ea52_ack) errorInfo(SUCCESS)",
     ("reply", "ChangeConfigCmd", REQ, "ack")),
    ("ConfigMessageProcessor",
     f"SENDING: Reply -  uuidRequest: {REQ}, CreateActiveProfileCmd, result:   status(Ea52_nak_alreadyExists) errorInfo(x)",
     ("reply", "CreateActiveProfileCmd", REQ, "nak_alreadyExists")),
    ("ConfigMessageProcessor", "PUBLISHING: Update -  umf_cfg:  cfgContextHash: abc, #configs: 1",
     ("publish", "Update", None, None)),
    ("QueryWorker",
     f" - Received Request and sent 1st Cmd Status for responseuuid .....{REQ}REQUEST: cmd://cmd/retro/get[@cmdVer='1'], reqEpId:{REQ2}",
     ("request", "cmd://cmd/retro/get", REQ, None)),
    ("QueryWorker", f"{REQ} - result data response size for the query - > 1",
     ("response", "query result", REQ, None)),
    ("Requester$RespDispatchRunner",
     f"Tossing unexpected RESPONSE: cmd://cmd/util/ping[@cmdVer='3'], status:Ea52_ack, reqEpId:{REQ2}, respEpId:{REQ2}, reqId:{REQ}, respId:{REQ2}",
     ("response", "cmd://cmd/util/ping", REQ, "ack")),
    ("PingRequestHandler", "Request/Response Timeout observed during the ping scheduler execution",
     ("timeout", "cmd://cmd/util/ping", None, None)),
    ("HealthCheckRequestCallback", "Received health-check request", ("request", "health-check", None, None)),
    ("HealthCheckRequestCallback", "Successfully responded to Hl7outbound health-check request",
     ("reply", "health-check", None, None)),
    ("LicenseManager", "Starting the Healthcheck request", ("request", "health-check", None, None)),
    ("ResourceDownloadRequestTask", "Request received for resource:qos/a/b.xml",
     ("request", "resource download", None, None)),
    ("SiteConfigAPIClient", "Response Status for fetching Site Configs : 200 OK",
     ("response", "fetching Site Configs", None, "200")),
    ("OSUpdateManager", "Socket Response for Input : -config ntp 10.0.0.1, Response : SUCCESS",
     ("response", "-config ntp", None, "SUCCESS")),
])
def test_classify_known_shapes(component, message, expected):
    info = classify(message, component)
    assert (info.kind, info.command, info.correlation_id, info.status) == expected


def test_patient_ids_are_never_captured():
    info = classify(f"Start publishing the data with stream id: {REQ} for patient {PATIENT}", "SyncCondition")
    assert info.kind == "publish" and info.correlation_id == REQ
    assert PATIENT not in str(vars(info))


def test_unknown_shapes_get_a_kind_from_keywords_only():
    assert vars(classify("Got a reply from the gateway", "SomethingNew")) == {
        "kind": "reply", "command": None, "correlation_id": None, "status": None}
    assert classify("Command timed out after 30s", "Other", "WARN").kind == "timeout"


def test_timeout_keyword_only_counts_on_warn_and_error():
    assert classify("Command timed out after 30s", "Other", "ERROR").kind == "timeout"
    assert classify("Socket timeout set to 30 s", "Other", "INFO") is None
    assert classify("Socket timeout set to 30 s", "Other") is None
    # The rule table is not limited: the ping scheduler logs its timeouts as ERROR anyway,
    # but a known shape is a known shape at any level.
    assert classify("Request/Response Timeout observed", "PingRequestHandler", "INFO").kind == "timeout"


@pytest.mark.parametrize("component,message,field,kind", [
    ("SiteConfigAPIClient",
     'Response Status for fetching Site Configs : 500 {"patientName":"John Smith","mrn":"12345"}', "status", "response"),
    ("ResourceDownloadRequestTask", "Request received for resource:/patients/John_Smith/report.pdf", "command", "request"),
])
def test_phi_in_free_text_never_reaches_parsed_fields(component, message, field, kind):
    info = classify(message, component, "INFO")
    assert info.kind == kind
    for value in (info.command, info.status, info.correlation_id):
        assert value is None or ("John" not in value and "Smith" not in value and "12345" not in value)
    if field == "status":
        assert info.status == "500"
    else:
        assert info.command == "resource download"


def test_values_outside_the_safe_shapes_are_dropped():
    # A config command name is a word; anything else in that slot is not kept.
    info = classify(f"SENDING: Reply -  uuidRequest: {REQ}, ChangeConfigCmd, result:   status(John Smith) errorInfo(x)",
                    "ConfigMessageProcessor")
    assert info.kind == "reply" and info.status is None
    info = classify(f"Tossing unexpected RESPONSE: cmd://persist/patient/12345, status:Ea52_ack, reqId:{REQ}",
                    "Requester$RespDispatchRunner")
    assert info.command is None or "12345" not in info.command


@pytest.mark.parametrize("message", [
    "Renaming thread Thread-1 to main:com.ge.x.SyslogRequestHandler@1a2b",   # 'Request' inside a class name
    "Heartbeat received",
    "",
])
def test_ordinary_lines_are_not_exchanges(message):
    assert classify(message, "AxoneNetSystem") is None


# --- parser and pipeline -----------------------------------------------------------------

HEADER = ("timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,sourceUUID,"
          "friendlyName,processInfo,sourceInfo,eventCategory,description")


def _csv(rows):
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n")
    out.write(HEADER + "\n")
    for ts, source_info, category, message in rows:
        w.writerow([ts, "0", "1", "INFO", "host-a", "dev-1", "Hub-01", "axone-configMain[28410:52]",
                    source_info, category, message])
    return out.getvalue()


ROWS = [
    ("2024-06-02T10:00:00.000+0000", CFG, "General", f"RECEIVED: Request - uuidRequest: {REQ}, ChangeConfigCmd, x"),
    ("2024-06-02T10:00:00.012+0000", CFG, "General",
     f"SENDING: Reply -  uuidRequest: {REQ}, ChangeConfigCmd, result:   status(Ea52_ack) errorInfo(ok)"),
    ("2024-06-02T10:00:01.000+0000", "StartupService.java:88", "PerfData", "request count=5"),   # telemetry
    ("2024-06-02T10:00:02.000+0000", ":0", "General", "Heartbeat received"),
]


def test_parser_fills_the_new_fields(tmp_path):
    path = tmp_path / "logs.csv"
    path.write_text(_csv(ROWS))
    events = list(portrait_csv.parse(path))
    req, rep, perf, plain = events
    assert (req.component, req.message_kind, req.command, req.correlation_id) == (
        "ConfigMessageProcessor", "request", "ChangeConfigCmd", REQ)
    assert (rep.message_kind, rep.status) == ("reply", "ack")
    assert req.process == "axone-configMain" and req.category == "General"   # category kept
    assert perf.component == "StartupService" and perf.message_kind is None   # PerfData never classified
    assert plain.component is None and plain.message_kind is None


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr("app.auth.is_locked", lambda *_: False)
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
    seed = Session()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def test_pipeline_stores_the_fields(env, tmp_path):
    _, db = env
    path = tmp_path / "logs.csv"
    path.write_text(_csv(ROWS))
    pipeline.ingest_file(path, db)
    stored = db.scalars(select(EventRow).where(EventRow.correlation_id == REQ).order_by(EventRow.id)).all()
    assert [(e.message_kind, e.status) for e in stored] == [("request", None), ("reply", "ack")]


# --- sequences ---------------------------------------------------------------------------

T0 = datetime(2024, 6, 2, 10, 0, 0)


def row(i, seconds, component, kind, corr=None, status=None, command="Cmd", device="Hub-01"):
    return SimpleNamespace(id=i, timestamp=T0 + timedelta(seconds=seconds), device_id=device, component=component,
                           message_kind=kind, command=command, correlation_id=corr, status=status)


NOW = T0 + timedelta(hours=1)


def test_id_pairing_answered_nak_unanswered_pending_unexpected():
    rows = [
        row(1, 0, "ConfigMessageProcessor", "request", "a"),
        row(2, 0.2, "ConfigMessageProcessor", "reply", "a", "ack"),
        row(3, 1, "ConfigMessageProcessor", "request", "b"),
        row(4, 1.1, "ConfigMessageProcessor", "reply", "b", "nak_alreadyExists"),
        row(5, 2, "ConfigMessageProcessor", "request", "c"),                    # never answered
        row(6, 3, "Requester$RespDispatchRunner", "response", "d", "ack"),     # no request
    ]
    seqs = {s.correlation_id: s for s in build_sequences(rows, NOW)}
    assert seqs["a"].state == "answered" and seqs["a"].latency_ms == 200
    assert seqs["b"].state == "nak"
    assert seqs["c"].state == "unanswered"
    assert seqs["d"].state == "unexpected"
    recent = build_sequences([row(7, 0, "ConfigMessageProcessor", "request", "e")], T0 + timedelta(seconds=10))
    assert recent[0].state == "pending"   # too recent to call lost


def test_next_reply_pairing_without_ids():
    rows = [
        row(1, 0, "HealthCheckRequestCallback", "request"),
        row(2, 0.003, "HealthCheckRequestCallback", "reply"),
        row(3, 10, "HealthCheckRequestCallback", "request"),
        row(4, 20, "HealthCheckRequestCallback", "request"),   # the request at 10 s got no reply
        row(5, 20.5, "HealthCheckRequestCallback", "reply"),
        row(6, 30, "HealthCheckRequestCallback", "request", device="Hub-02"),   # other device, separate
    ]
    states = [(s.request_event_id, s.state) for s in build_sequences(rows, NOW)]
    assert states == [(1, "answered"), (3, "unanswered"), (4, "answered"), (6, "unanswered")]


def test_components_that_never_log_replies_are_not_sequences():
    assert build_sequences([row(1, 0, "LicenseManager", "request")], NOW) == []


# --- the API -----------------------------------------------------------------------------

def _seed_exchange(db, tmp_path):
    path = tmp_path / "logs.csv"
    path.write_text(_csv(ROWS + [
        ("2024-06-02T10:05:00.000+0000", CFG, "General", f"RECEIVED: Request - uuidRequest: {REQ2}, RemoveActiveProfileCmd, x"),
        ("2024-06-02T10:05:00.000+0000", HC, "General", "Received health-check request"),
        ("2024-06-02T10:05:00.004+0000", HC.replace(":61", ":112"), "General",
         "Successfully responded to Hl7outbound health-check request"),
    ]))
    pipeline.ingest_file(path, db)


def test_sequences_endpoint(env, tmp_path):
    client, db = env
    _seed_exchange(db, tmp_path)
    body = client.get("/api/logs/sequences").json()
    assert body["counts"]["answered"] == 2 and body["counts"]["unanswered"] == 1
    by_cmd = {c["command"]: c for c in body["by_command"]}
    assert by_cmd["ChangeConfigCmd"]["median_latency_ms"] == 12
    only = client.get("/api/logs/sequences", params={"state": "unanswered"}).json()
    assert only["total"] == 1 and only["items"][0]["command"] == "RemoveActiveProfileCmd"
    assert client.get("/api/logs/sequences", params={"state": "lost"}).status_code == 400
    audit = db.scalars(select(AuditLogEntry).where(AuditLogEntry.action == "logs_sequences_view")).all()
    assert audit and REQ not in audit[-1].detail   # filter names only, never values


def test_search_by_exchange_fields(env, tmp_path):
    client, db = env
    _seed_exchange(db, tmp_path)
    exchange = client.get("/api/logs/events", params={"correlation_id": REQ.upper(), "order": "asc"}).json()
    assert [e["message_kind"] for e in exchange["items"]] == ["request", "reply"]
    replies = client.get("/api/logs/events", params={"message_kind": "reply"}).json()
    assert replies["total"] == 2
    assert client.get("/api/logs/events", params={"message_kind": "bogus"}).status_code == 400
    facets = client.get("/api/logs/facets").json()
    assert {f["value"]: f["count"] for f in facets["message_kinds"]}["request"] == 3


def test_reply_logged_before_its_request_is_flagged_not_negative():
    rows = [
        row(1, 5, "ConfigMessageProcessor", "request", "a", device="Hub-01"),
        row(2, 4.5, "ConfigMessageProcessor", "reply", "a", "ack", device="Hub-02"),   # Hub-02's clock is behind
    ]
    (seq,) = build_sequences(sorted(rows, key=lambda r: r.timestamp), NOW)
    assert seq.state == "answered" and seq.clock_skew and seq.latency_ms is None
    from app.storage.sequences import summarize
    _, stats = summarize([seq])
    assert stats[0].median_latency_ms is None and stats[0].max_latency_ms is None


def _add_raw(db, seconds, **kw):
    fields = dict(timestamp=T0 + timedelta(seconds=seconds), source="portrait", severity="INFO", message="m",
                  device_id="Hub-01", category="General", process="svc", source_file="x.csv", source_line=1,
                  component="ConfigMessageProcessor", correlation_id=REQ)
    fields.update(kw)
    db.add(EventRow(**fields))
    db.commit()


def test_mask_covers_the_parsed_fields_in_events_and_sequences(env):
    # Values like these can no longer be parsed out, but rows from before the
    # bounds (or a future rule mistake) must still be masked on the way out.
    client, db = env
    _add_raw(db, 0, message_kind="request", command="patientName=John Smith")
    _add_raw(db, 1, message_kind="reply", command="ChangeConfigCmd", status="patientName=Jane Roe")
    events = client.get("/api/logs/events", params={"mask": "true"}).json()["items"]
    text = str(events)
    assert "John Smith" not in text and "Jane Roe" not in text and "[REDACTED:" in text
    assert all(e["masked"] for e in events)
    assert "John Smith" in str(client.get("/api/logs/events").json())          # mask is opt-in, as for message
    seqs = client.get("/api/logs/sequences", params={"mask": "true"}).json()
    assert "John Smith" not in str(seqs) and "Jane Roe" not in str(seqs)
    assert seqs["items"][0]["command"].startswith("patientName=[REDACTED:")


def test_existing_database_is_upgraded_in_place(tmp_path, monkeypatch):
    """A database from before message parsing (and before the PHI scan columns)
    starts, gets the new columns and indexes, and keeps its events and audit log."""
    from sqlalchemy import inspect, text
    from app import db as dbmodule

    old = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with old.begin() as c:
        c.execute(text("""CREATE TABLE normalized_events (
            id INTEGER NOT NULL PRIMARY KEY, job_id VARCHAR, timestamp DATETIME NOT NULL, device_id VARCHAR,
            source VARCHAR NOT NULL, source_file VARCHAR, source_line INTEGER, category VARCHAR,
            severity VARCHAR NOT NULL, process VARCHAR, message TEXT NOT NULL)"""))
        c.execute(text("INSERT INTO normalized_events (id, timestamp, source, severity, message) "
                       "VALUES (1, '2024-06-02 10:00:00', 'portrait', 'INFO', 'old line')"))
        c.execute(text("""CREATE TABLE audit_log (id INTEGER NOT NULL PRIMARY KEY, timestamp DATETIME NOT NULL,
            actor VARCHAR NOT NULL, session_id VARCHAR, action VARCHAR NOT NULL, detail TEXT)"""))
        c.execute(text("INSERT INTO audit_log (id, timestamp, actor, action) "
                       "VALUES (1, '2026-11-02 10:00:00', 'engineer', 'connect')"))

    monkeypatch.setattr(dbmodule, "engine", old)
    dbmodule.init_db()

    insp = inspect(old)
    columns = {c["name"] for c in insp.get_columns("normalized_events")}
    assert {"component", "message_kind", "command", "correlation_id", "status"} <= columns
    indexes = {i["name"] for i in insp.get_indexes("normalized_events")}
    assert {"ix_events_correlation", "ix_events_kind_ts"} <= indexes
    with old.connect() as c:
        assert c.execute(text("SELECT message, message_kind FROM normalized_events")).one() == ("old line", None)
        assert c.execute(text("SELECT actor FROM audit_log")).scalar_one() == "engineer"
    old.dispose()
