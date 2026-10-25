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
                         AcquiredFileRecord, IngestedFile, AuditLogEntry
  schemas.py             Pydantic request/response models
  auth.py                 Local-demo bearer auth + in-memory session activity tracking
  audit.py                 Audit log helper, used everywhere an action happens

  orchestrator/
    routes.py            HTTP endpoints: connect, discover, jobs, findings, package
    jobs.py               Job state machine (queued -> running -> done/failed), run in
                          the background after POST /jobs; results.json per job

  collectors/
    base.py               Plugin contract: configure(), run(), cleanup(), resource limits
    registry.py            The list of plugins; the orchestrator looks them up by name
    k8s.py                 Starts, execs into and deletes tool pods in our namespace
    netshoot.py, tshark.py    Working (SCRUM-39), see "Tool plugins" below
    rti.py, hub_import.py     Stubs
                          To add a tool: one new file here + one line in PLUGINS in registry.py.

  uploads.py             Files a field engineer hands in (POST /api/uploads), e.g. a .pcap

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

  storage/               Central log storage + unified log view (see below)
    filters.py            Search filters, time rules, paging cursors (no database)
    completeness.py        "Is every entry there?" verdict (no database)
    events.py               Queries: search, facets, collections, completeness summary
    removal.py               remove_events(): the only way to delete stored events
    routes.py                /api/logs endpoints
    schemas.py                Response models for those endpoints

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

Use Python 3.10 through 3.13. The source uses Python 3.10 union type syntax,
and the currently pinned Pydantic release does not support Python 3.14.
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

## Connectivity preflight

Before starting diagnostics, the backend can run bounded TCP checks against a
target host. The endpoint returns HTTP 200 even when the target is unreachable;
inspect the overall status and each port result for the observed reason.

```bash
curl -X POST http://localhost:8000/api/connectivity-check \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{"target":"192.168.0.3","ports":[22,443,6443],"timeout_seconds":3}'
```

A timeout is reported as potentially filtered or unresponsive. TCP alone cannot
prove that a remote firewall blocked the port. Requests are limited to 16 unique
ports and a maximum 10-second timeout per port.
DNS resolution uses the same configured timeout. TCP checks use a shared pool
of at most eight workers across all requests, so an otherwise idle request for
16 ports takes at most two TCP rounds (plus DNS): about 30 seconds at the
maximum timeout. Under concurrent load, a request may also wait for shared
worker capacity.
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
  edges. All devices: cut at every hour boundary of the API's clock
  (`AXONE_TIMEZONE`; both timestamps must be in the same hour there), so 09:30 to 11:15 is 09:30-10:00, 10:00-11:00, 11:00-11:15.
  One device: chunks of at most 7 days.
- The end is held back by `AXONE_SETTLE_MINUTES` (default 15) for logs that
  devices upload late; the response's `notes` says when that happened.
- "Only new" compares time, not request ids: after pulling 09:00-10:00, a
  pull of 09:30-10:30 only requests 10:00-10:30. Windows that failed are
  requested again. One call at a time, with a short pause between calls.

Device selection (SCRUM-70): pull one, several or all devices in one run
with `"device_uuids": [...]` on the acquire request (empty = all devices).
Selected devices use the per-device endpoint, up to 7 days per request, and
"already pulled" is tracked per device, so adding a device to a selection
only fetches that device. At most 50 devices per pull.

Each log line is stored once, whatever order pulls happen in: a device pull
skips time an all-devices pull already covered, and when an all-devices
window is ingested, rows earlier single-device pulls stored for that time are
replaced (the response's `notes` says so). The same holds for an event
category inside a broader pull. Known limit: a device-only pull and a
category-only pull overlap without either containing the other, so lines in
both (that device AND that category) can be stored twice; the API's category
codes have no confirmed name mapping yet, so they can't be matched to rows.

The API cannot list devices, so the tool keeps its own list
(`known_devices` table):

```
GET  /api/sources/devices      devices to pick from: ID, latest name, host, first/last seen
POST /api/sources/devices      {"device_uuid": "...", "name": "..."} add a device by hand
```

It fills itself from every Portrait-format CSV acquired (log export API,
ZIP / folder import, Box): no extra calls to the live system, works offline.
Devices are keyed on their ID and the newest friendly name wins (devices get
renamed). A device that has never appeared in pulled logs can be added by
hand; once real logs mention it, the logged name replaces the typed one.
Adding a device that is new is written to the audit log (`device_add`);
adding one already in the list changes nothing and is not logged. Only files
that were acquired or ingested successfully add devices to the list.

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

## Central log storage and the unified log view

Every log that goes through `ingestion.pipeline.ingest_file` ends up in one
table, `normalized_events`, in one shape, whichever source, parser or device
it came from. The `/api/logs` endpoints are how you read it back. All of them
need a signed-in, unlocked session.

```
GET /api/logs/events                        search everything, newest first
GET /api/logs/facets                        counts per source / severity / category / device
GET /api/logs/collections                   every collection (job) and how many events it holds
GET /api/logs/collections/{id}              is this collection complete? what is missing?
GET /api/logs/collections/{id}/events       unified log view: all its entries, oldest first
```

`{id}` is a job id, or `unassigned` for logs ingested without a job.

**Search.** One request searches every source and device at once. Filters
combine (AND): `since` (inclusive) / `until` (exclusive) as ISO times (no
timezone means UTC), `device`, `source`, `category`, `severity` (each can be
repeated), `min_severity` (e.g. `WARN` = WARN and ERROR), `process`,
`source_file`, `job_id`, and `q` (case-insensitive text in the message).
`order=asc|desc` and `limit` (1-1000, default 100). Paging uses a cursor:
send the previous page's `next_cursor` back as `cursor`. The cursor is
stable when new logs arrive between pages, which `offset` is not. `total`
is returned on the first page only.

```bash
# everything at WARN or above, from any device, since a given time
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/logs/events?min_severity=WARN&since=2024-06-02T00:00:00Z"
# hide the noisy category: ask /facets what is there, then filter the rest
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/logs/facets"
```

**Unified log view.** `/collections/{id}/events` returns every entry of one
collection as a single time-ordered stream across all its files, sources and
devices. "Every entry is there" is checked, not assumed:

- Each file stored by ingestion leaves a receipt (`ingested_files` table:
  rows read, events stored, rows rejected), written in the same transaction as
  its events.
- `GET /api/logs/collections/{id}` compares those receipts with what is in the
  store now and with the acquisition manifest. `complete` is `true` only if
  there are no `problem` issues. Problems it reports, each with the file and
  the count: rows that could not be converted (`rows_rejected`), events missing
  from the store (`store_mismatch`), files that failed, were unrecognised, hit
  the size limit or were never ingested (`file_not_stored`), events with no
  receipt (`untracked_events`), a collection that is still running or failed,
  or one with no logs at all. For `unassigned` (logs ingested without a job,
  which is every pull through `/api/sources` today) the acquisition records
  that have no job are checked too, so one failed file in such a pull makes it
  incomplete.
- Events removed on purpose (see below) show as an `info` note,
  `events_removed`, not as a problem.
- The first page of `/events` has `total`: it equals `events_stored` in the
  report when no filters are used.

**Rejected rows: open decision.** Ingest accepts a file if up to 1% of its rows
cannot be converted, and those rows are not stored. Today a rejected row is a
`problem`, so `complete` is `false` and the issue names the file and the count.
That matches the story's "none left out", but it means one bad row marks a
collection incomplete for good. Whether rejected rows should instead be
reported with counts without flipping `complete` is a product-owner decision
that has not been made. Until it is, the stricter behaviour stays; changing it
is one line in `storage/completeness.py`.

**Removing events on purpose.** Never delete rows from `normalized_events`
directly: the receipts would say events were lost. Call
`storage.removal.remove_events(db, event_ids)` instead. It deletes the events
and records the count on each file's receipt (`events_removed`), in one
transaction, and returns how many were deleted. SCRUM-70's duplicate removal
(a device pull followed by an all-devices pull) should use it.

A freshly cloned database has no receipts for logs ingested before this
feature, so those show as `untracked_events`. They are development data:
delete `portrait_iq.db` and re-ingest.

Searches and unified-view requests are written to the audit log (who, which
filters were used, how many matched). Filter values, including the `q` text,
are never recorded, because a search term can be a patient identifier.

**PHI/PII.** `message` is raw log text. In the sample logs it contains patient
identifiers. The API returns it as is, so treat any screen, export or
screenshot of it as sensitive, and never paste it into issues, PRs or chat.

Limits to know about:

- `q` is a substring match (`LIKE`), which scans messages. Fine at the
  ~550,000 events in the 35 sample files; combine it with `since`/`until` or
  `source` to keep it fast. A real full-text index (SQLite FTS5 or Postgres)
  is the next step if searches get slow.
- Device ids are not reconciled across sources yet (a Portrait `hostName`, a
  pod name and an IP are different values), so filtering by `device` finds one
  naming scheme at a time. Category names are not unified either; use
  `/facets` to see what exists.
- `complete` says nothing was lost after the logs were read. It cannot say
  that the source gave us everything (for example a device that never
  uploaded an hour).
- New tables and indexes: `init_db` creates the `ingested_files` table and the
  two new indexes on an existing database, so nothing needs deleting just for
  that. The standalone `device_id` index is gone because `(device_id, timestamp)`
  covers it; a database created earlier keeps the old one, which is harmless.

## Tool plugins (SCRUM-39)

Start a job with `POST /api/jobs?target_ip=...`. It returns at once with
status `queued`; poll `GET /api/jobs/{id}`, then read each plugin's summary,
warnings and files from `GET /api/jobs/{id}/results`. Files land in
`JOB_OUTPUT_DIR/<job id>/<plugin>/` (default `./data/jobs`, gitignored).

**Netshoot** (`{"tools": ["netshoot"]}`) starts one pod from
`NETSHOOT_IMAGE` in `TOOLS_NAMESPACE` (default `portrait-iq`), runs
interface, route and DNS checks plus ping, traceroute and TCP port checks
against the target, and deletes the pod. Options: `ports`, `dns_names`,
`extra_targets`. A failed check is a warning, not a failed job.

**tshark** has two modes:

- `import`: upload the capture first with
  `POST /api/uploads?kind=pcap` (multipart field `file`), then pass
  `{"tshark": {"mode": "import", "upload_id": "..."}}`. Runs `editcap` and
  `tshark -r` where the backend runs, so install Wireshark's command-line
  tools there (Ubuntu `apt install tshark`, macOS `brew install wireshark`,
  Windows: the Wireshark installer, then set `WIRESHARK_BIN_DIR`).
- `run`: a live capture from a host-network pod (`TSHARK_IMAGE`, built from
  `deploy/images/tshark`). Refused unless `TSHARK_LIVE_CAPTURE_ENABLED=true`,
  because the pod gets raw-socket rights on the customer's node. Options:
  `duration_s` (5 to 900), `interface`, and exactly one of `filter_preset`
  or `capture_filter` (a live capture never takes everything). Presets:
  `rti_all` (UMF DDS domains 7, 12 and 13), `rti_logging` (7),
  `rti_source_pod` (12), `rti_view_pod` (13). `capture_filter` is a BPF
  filter for anything else, e.g. `host 192.0.2.1`.

Both keep headers only (first 128 bytes per packet) unless `headers_only`
is false with a `full_payload_reason`.

**UMF RTI** (`{"tools": ["rti"]}`) starts a host-network pod from GE's
rtitools image (`RTI_IMAGE`) and runs `rtiddsspy` on one DDS domain for
`duration_s` (10 to 300, default 30), with the target as discovery peer.
Options: `domain` (7 logging, 12 source pod, 13 view pod; default 12). It
reports hosts, topics and types only, never sample contents. Refused unless
`RTI_ENABLED=true`: the image must come from GE (it carries Connext Pro and
the domain's security files) and the site must approve a listen-only
participant. `RTI_QOS_FILE` / `RTI_QOS_PROFILE` pick the QoS profile with
the domain's security settings. The spy output parser is based on RTI's
docs and still needs checking against real output on the bench.

Kubernetes access: in the cluster the backend uses its service account
(`deploy/k8s/tool-rbac.yaml`: pods and pods/exec in its own namespace only).
From a laptop it uses `KUBECONFIG_PATH` or `~/.kube/config`. Tool pods are
labelled `portrait-iq/role=tool`, stop themselves at a deadline, and any
left over from a crash are deleted when the backend starts. The lab is
offline, so import `NETSHOOT_IMAGE` and `TSHARK_IMAGE` into the node's
containerd the same way as the backend image.

PHI/PII collection point: captures (`capture.pcapng`) and uploads can hold
patient data even with headers only. They stay in the gitignored data
folders and are never logged; the audit log records upload ids, not file
names.

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
- Real device discovery/SSH (Kubernetes access exists only for tool pods, `collectors/k8s.py`)
- The UMF RTI and hub import plugins
- The compute-platform log parser (`parsers/compute_csv.py` — need a sample)
- Manual/documentation matching (`knowledge/lookup.py` always returns unconfirmed)
- Downloading a whole collection as CSV/NDJSON (the unified view pages through it; an export route would reuse `storage.events.iter_events`)
- Full-text search index, and reconciled device ids / category names across sources
- Analytics endpoints (`analytics/service.py` has no route pointing to it)
- Packaging the final zip artifact (`orchestrator/routes.py::package` is a stub)

Each of these is a natural next PR and doesn't require touching the others —
that separation is the whole point of the layering.
