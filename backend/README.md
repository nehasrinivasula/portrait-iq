# Portrait IQ backend — skeleton

This is a starting skeleton, not a finished backend. It's structured to match the
five-layer architecture we discussed: frontend → orchestrator API → collectors →
event store → analysis engine, with knowledge lookup and analytics as separate,
pluggable consumers.

Nothing here has been run against real lab hardware. Treat every `TODO` as a
task, and treat anything that *does* run (the CSV parser, the rule thresholds)
as provisional, built from the 35 sample log files, not a full spec.

## SCRUM-32 — Login: frontend handoff

This branch provides the backend contract for the Login UI. It is a **local
demo** authentication flow only; it is not GE SSO and must not use GE or
hospital credentials.

1. Send `POST /api/auth/login` with JSON:

   ```json
   {"username": "your-demo-user", "password": "your-demo-password"}
   ```

   A successful response is `200` and contains:

   ```json
   {"access_token": "<signed-token>", "token_type": "bearer", "expires_in": 3600}
   ```

2. Store the token only for the active local-demo session and use it to check
   whether the user is signed in: `GET /api/auth/me` with
   `Authorization: Bearer <access_token>`. A `200` response returns the demo
   user's `username` and `name`.

3. Send the same `Authorization` header on every diagnostic request:
   `/api/connect`, `/api/devices`, `/api/jobs`, job status, findings, and
   package export. `GET /health` intentionally needs no token.

Invalid login credentials return `401` with `Invalid username or password.`
Missing tokens return `401` with `Authentication required.` Invalid or expired
tokens return `401` with `Invalid or expired bearer token.` If the backend demo
credentials have not been configured, login returns `503`.

The implementation is in `app/auth.py`; route definitions are in `app/main.py`;
the request and response shapes are in `app/schemas.py`. This dependency
boundary is deliberate so a real identity provider can replace the local-demo
implementation later without changing the frontend's protected-route pattern.

## Layout

```
app/
  main.py            FastAPI app entrypoint
  config.py           Settings (DB path, resource limit defaults)
  db.py                SQLAlchemy engine/session setup
  models.py             ORM tables: Device, Job, NormalizedEvent, Signal,
                         AcquiredFileRecord, AuditLogEntry
  schemas.py             Pydantic request/response models
  auth.py                 Local-demo bearer auth + in-memory session activity tracking
  audit.py                 Audit log helper, used everywhere an action happens

  orchestrator/
    routes.py            HTTP endpoints: connect, discover, jobs, findings, package
    jobs.py               Job state machine (queued -> running -> done/failed)

  collectors/
    base.py               Plugin contract: configure(), run(), cleanup(), resource limits
    registry.py            The list of plugins; the orchestrator looks them up by name
    netshoot.py, rti.py, tshark.py, hub_import.py    One file per tool. All stubs.
                          To add a tool: one new file here + one line in PLUGINS in registry.py.

  sources/               Log acquisition (SCRUM-43): where raw log files come from
    base.py               LogSource interface every source implements
    registry.py            The list of sources; add one line here per new source
    local_import.py         Local file / folder / ZIP / upload. Works offline
    axone_client.py          Client for GE's log export API (all 8 endpoints)
    axone_export.py          Log source that pulls hour by hour from that API
    box.py                   Optional Box folder (needs internet + credentials)
    service.py                acquire(): only-new-files, limits, safe ZIPs, audit
    routes.py                  /api/sources endpoints

  ingestion/
    schema.py             NormalizedEvent — the one shape everything downstream reads
    parsers/
      portrait_csv.py     Real parser, built from the 35 sample files you uploaded
      compute_csv.py      Stub — we don't have a sample of this format yet
    pipeline.py            Glue: find files, pick a parser, write normalized events

  analysis/
    rules.py               The 5 findings from the sample logs, as rule objects
    engine.py                Runs rules over events, writes Signal rows

  knowledge/
    lookup.py               Signal -> manual entry. Returns "unconfirmed" until
                             real manuals are ingested — see Marco's feedback,
                             this must never silently invent a confirmed match.

  analytics/
    service.py               Stub only. Not wired into any route yet — this is
                             the Andrew Welch "how often is Hub A used" idea,
                             deliberately kept separate from the analysis engine.

tests/
  test_ingestion_pipeline.py   Proves the real parser works end to end
```

## This week's two features, in this skeleton

1. **Event store.** Live in `ingestion/` and `models.py::NormalizedEvent`. The
   parser in `parsers/portrait_csv.py` is real and tested — start by pointing
   `pipeline.ingest_directory()` at your own log exports and checking the numbers
   against what you already know from the 35 sample files.
2. **Real device connect.** Live in `orchestrator/routes.py` (`POST /connect`,
   `GET /discover`) and `collectors/base.py`. The current discovery stub returns
   nothing — that's the first thing to replace with an actual reachability check
   against your lab's MiniEdge P360 and Virtual Hub Host.

## Running it

Use Python 3.12 (the pinned pandas has no prebuilt wheel for 3.13).

```bash
cd backend
cp .env.example .env
# Edit .env: set DEMO_AUTH_USERNAME, DEMO_AUTH_PASSWORD, and AUTH_TOKEN_SECRET.
# Generate a token secret with: python -c "import secrets; print(secrets.token_urlsafe(32))"
pip install -r requirements.txt
uvicorn app.main:app --reload
# API docs at http://localhost:8000/docs
pytest tests/
```

## Login setup

This is a local demo implementation, not GE SSO. The one demo user's username,
password, and token signing secret are read from `backend/.env` (which is
gitignored). Do not use GE credentials or any real hospital account.

Log in, keep the returned bearer token, and send it with each diagnostic API
request:

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"your-demo-user","password":"your-demo-password"}'

curl http://localhost:8000/api/auth/me \
  -H 'Authorization: Bearer YOUR_ACCESS_TOKEN'
```

`GET /health` is public. Every diagnostic route (`/api/connect`, `/api/devices`,
`/api/jobs`, job status/findings, and package export) requires
`Authorization: Bearer <access_token>`. Invalid credentials and missing or
invalid tokens return safe `401` responses. Tokens expire after
`AUTH_TOKEN_TTL_SECONDS` (one hour by default).

## Log sources (SCRUM-43)

Every way of getting raw logs into the tool is a `LogSource`
(`app/sources/base.py`). The acquisition service lists what a source has in
the requested time range (maximum: last 30 days; default 30 days, or 24 hours
for the log export API), skips anything
already in the `acquired_files` manifest, downloads the rest into
`data/staging/`, unpacks ZIPs safely, and hands CSVs to
`ingestion.pipeline.ingest_file`. Running it twice pulls nothing new; a file
that changed at the source counts as new. One bad file is recorded as failed
and retried next run without losing the others. Every run is audit-logged.

| Source | Status | Needs |
|---|---|---|
| `local` | Working | Nothing. Path to a file/folder/ZIP, or upload through the API |
| `box` | Working against a mocked Box; untested against a real folder | Internet, `BOX_CLIENT_ID` / `BOX_CLIENT_SECRET`, `BOX_FOLDER_ID`, one of `BOX_ENTERPRISE_ID` / `BOX_USER_ID` |
| `axone` | Working against a fake of the API; to be verified on the lab system | `AXONE_BASE_URL` pointing at the log export API (see below) |

```
GET  /api/sources                  sources + whether each is usable (and why not)
POST /api/sources/{id}/test        connection check with a specific reason
GET  /api/sources/{id}/files       preview, flags files already pulled
POST /api/sources/{id}/acquire     {"since", "until", "options": {...}, "ingest": true}
POST /api/sources/local/upload     multipart files / ZIPs
```

To add a source: subclass `LogSource`, add a `register()` line in
`sources/registry.py`. Nothing else changes.

### Log export API (`axone`)

GE's Axone log export API (port 9200) is the preferred source: it returns the
same CSV format as the "all device log" exports. It only answers from inside
the Kubernetes cluster. Optional `options`:
`{"device_uuid": "...", "event_category": "3"}` (category is the API's
integer code; the name mapping is not confirmed yet).

Time-frame selection (SCRUM-44): a pull covers exactly `[since, until)` and
nothing outside it. Times are ISO 8601; a time without an offset is read as
UTC; the window must be within the last 30 days.

- The window is cut into requests the API accepts while keeping its exact
  edges. All devices: cut at every hour boundary (both timestamps must be in
  the same hour), so 09:30 to 11:15 is 09:30-10:00, 10:00-11:00, 11:00-11:15.
  One device: chunks of at most 7 days.
- The end is held back by `AXONE_SETTLE_MINUTES` (default 15) for logs that
  devices upload late; the response's `notes` says when that happened.
- "Only new" compares time, not request ids: after pulling 09:00-10:00, a
  pull of 09:30-10:30 only requests 10:00-10:30. Windows that failed are
  requested again. One call at a time, with a short pause between calls.

Reaching it from a laptop on the lab network (development only):

```bash
# 1. On the MiniEdge, as root: find the service, then forward it to the box itself
kubectl get svc -A | grep axone-log-export
kubectl port-forward -n pm svc/axone-log-export-tcp 9200:9200

# 2. On the laptop: carry that port over SSH
ssh -N -L 9200:localhost:9200 gehc@192.168.0.3

# 3. Start the backend pointed at it
AXONE_BASE_URL=http://localhost:9200 uvicorn app.main:app
```

Settings: `AXONE_BASE_URL`, `AXONE_TIMEOUT_SECONDS` (60),
`AXONE_REQUEST_DELAY_SECONDS` (0.5), `AXONE_TIMEZONE` (UTC, the timezone the
API's timestamps are in; confirmed on the lab system), `AXONE_DEFAULT_LOOKBACK_HOURS`
(24), `AXONE_SETTLE_MINUTES` (15, wait after an hour ends before pulling it,
for late-arriving logs). The 15 minutes is an estimate, not a measured
value: a device that was offline can upload its logs much later than that,
and an hour already pulled is not pulled again. Running inside
the cluster later only changes `AXONE_BASE_URL` to
`http://axone-log-export-tcp.pm.svc.cluster.local:9200`.

Security note: the local source's `path` option (preview, acquire) reads
any file or folder the server process can read. That is how thumb-drive
and folder imports work, and it is acceptable only while the backend runs
on the field engineer's own laptop bound to localhost (uvicorn's default,
127.0.0.1). Real authentication and a path allow-list must come before the
backend is ever exposed on a shared network or deployed as a pod.

PHI/PII collection point: acquired files are raw logs and may contain
patient or staff data. They stay in the gitignored staging folder and are
never written to the audit log.

## Data & privacy

Never commit real log exports, GE service manuals, branding assets, or
anything pulled from a real hospital deployment to this repo, even in a
private repo — treat GitHub as public. Sample logs used to build the
event-store parser and analysis rules live outside this repo; if your team
adds sample data for testing, put it in a gitignored folder (e.g. `data/`)
and share it through Google Drive, Slack, or whatever your team already
uses for GE-provided files instead.

## Deliberately not built yet

- GE SSO / production identity-provider integration (the current auth is local-demo only)
- Real device discovery/SSH/Kubernetes API calls (`collectors/base.py` TODOs)
- The compute-platform log parser (`parsers/compute_csv.py` — need a sample)
- Manual/documentation matching (`knowledge/lookup.py` always returns unconfirmed)
- Analytics endpoints (`analytics/service.py` has no route pointing to it)
- Packaging the final zip artifact (`orchestrator/routes.py::package` is a stub)

Each of these is a natural next PR and doesn't require touching the others —
that separation is the whole point of the layering.
