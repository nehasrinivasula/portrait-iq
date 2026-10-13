# Portrait IQ backend — skeleton

This is a starting skeleton, not a finished backend. It's structured to match the
five-layer architecture we discussed: frontend → orchestrator API → collectors →
event store → analysis engine, with knowledge lookup and analytics as separate,
pluggable consumers.

Nothing here has been run against real lab hardware. Treat every `TODO` as a
task, and treat anything that *does* run (the CSV parser, the rule thresholds)
as provisional, built from the 35 sample log files, not a full spec.

## Layout

```
app/
  main.py            FastAPI app entrypoint
  config.py           Settings (DB path, resource limit defaults)
  db.py                SQLAlchemy engine/session setup
  models.py             ORM tables: Device, Job, NormalizedEvent, Signal,
                         AcquiredFileRecord, AuditLogEntry
  schemas.py             Pydantic request/response models
  auth.py                 Stub auth + in-memory session lock (NOT real security yet)
  audit.py                 Audit log helper, used everywhere an action happens

  orchestrator/
    routes.py            HTTP endpoints: connect, discover, jobs, findings, package
    jobs.py               Job state machine (queued -> running -> done/failed)

  collectors/
    base.py               Collector interface: resource limits, run(), cleanup()
    netshoot.py, rti.py, tshark.py, hub_import.py    One file per tool. All stubs.

  sources/               Log acquisition (SCRUM-43): where raw log files come from
    base.py               LogSource interface every source implements
    registry.py            The list of sources; add one line here per new source
    local_import.py         Local file / folder / ZIP / upload. Works offline
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
pip install -r requirements.txt
uvicorn app.main:app --reload
# API docs at http://localhost:8000/docs
pytest tests/
```

## Log sources (SCRUM-43)

Every way of getting raw logs into the tool is a `LogSource`
(`app/sources/base.py`). The acquisition service lists what a source has in
the requested time range (default and maximum: last 30 days), skips anything
already in the `acquired_files` manifest, downloads the rest into
`data/staging/`, unpacks ZIPs safely, and hands CSVs to
`ingestion.pipeline.ingest_file`. Running it twice pulls nothing new; a file
that changed at the source counts as new. One bad file is recorded as failed
and retried next run without losing the others. Every run is audit-logged.

| Source | Status | Needs |
|---|---|---|
| `local` | Working | Nothing. Path to a file/folder/ZIP, or upload through the API |
| `box` | Working against a mocked Box; untested against a real folder | Internet, `BOX_CLIENT_ID` / `BOX_CLIENT_SECRET`, `BOX_FOLDER_ID`, one of `BOX_ENTERPRISE_ID` / `BOX_USER_ID` |
| Portrait diags | Not built yet | Waiting on what the `/portrait/diags` export requests look like |

```
GET  /api/sources                  sources + whether each is usable (and why not)
POST /api/sources/{id}/test        connection check with a specific reason
GET  /api/sources/{id}/files       preview, flags files already pulled
POST /api/sources/{id}/acquire     {"since", "until", "options": {...}, "ingest": true}
POST /api/sources/local/upload     multipart files / ZIPs
```

To add a source: subclass `LogSource`, add a `register()` line in
`sources/registry.py`. Nothing else changes.

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

- Real auth (`auth.py` is a stub — anyone is "authenticated")
- Real device discovery/SSH/Kubernetes API calls (`collectors/base.py` TODOs)
- The compute-platform log parser (`parsers/compute_csv.py` — need a sample)
- Manual/documentation matching (`knowledge/lookup.py` always returns unconfirmed)
- Analytics endpoints (`analytics/service.py` has no route pointing to it)
- Packaging the final zip artifact (`orchestrator/routes.py::package` is a stub)

Each of these is a natural next PR and doesn't require touching the others —
that separation is the whole point of the layering.