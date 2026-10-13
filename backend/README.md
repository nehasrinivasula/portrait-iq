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
  models.py             ORM tables: Device, Job, NormalizedEvent, Signal, AuditLogEntry
  schemas.py             Pydantic request/response models
  auth.py                 Local-demo bearer auth + in-memory session activity tracking
  audit.py                 Audit log helper, used everywhere an action happens

  orchestrator/
    routes.py            HTTP endpoints: connect, discover, jobs, findings, package
    jobs.py               Job state machine (queued -> running -> done/failed)

  collectors/
    base.py               Collector interface: resource limits, run(), cleanup()
    netshoot.py, rti.py, tshark.py, hub_import.py    One file per tool. All stubs.

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
  test_ingestion_pipeline.py   One test, proves the real parser works end to end
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
