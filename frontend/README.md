# Portrait IQ Frontend

This React/Vite frontend provides the local-demo user interface for Portrait
IQ. This document records the log-filtering integration contract so the UI,
log-acquisition, and filtering work can be integrated without exposing lab
credentials or raw diagnostic logs.

## Run locally

From `frontend/`:

```bash
npm install
npm run dev
```

The frontend expects the Portrait IQ backend to be running locally. Sign in
with the local-demo credentials configured in the backend environment; do not
put those credentials in this repository.

After signing in, open **Log filters** from the prerequisites page. The route
is protected, like the other diagnostic screens.

## SCRUM-54: filter controls

`src/features/logs/` contains the SCRUM-54 UI work:

- `LogFilterControls.tsx` renders the controls, active-filter chips, individual
  clear actions, and **Clear all**.
- `logFilters.ts` owns the typed UI state and translates it to the SCRUM-53
  API query contract.
- `LogFiltersPage.tsx` is a protected review route. Its results panel is
  intentionally a placeholder until SCRUM-53 connects real results.

The controls are client-side state only. They do not fetch, store, or display
raw GE diagnostic data.

## Log-query contract (SCRUM-53)

When the results implementation is connected, it must call the authenticated
endpoint below. Every non-empty filter is an exact-match filter, and all
selected filters are combined with AND.

```text
GET /api/logs?device_id={deviceId}&severity={severity}&category={category}
             &start={UTC ISO-8601}&end={UTC ISO-8601}&limit={limit}&offset={offset}
Authorization: Bearer {Portrait IQ token}
```

| UI control | Query parameter | Rule |
| --- | --- | --- |
| Device ID | `device_id` | Exact source-specific device identifier. |
| Severity | `severity` | One of `DEBUG`, `INFO`, `WARN`, or `ERROR`. |
| Category | `category` | Exact normalized category string, for example a source-defined category such as `PerfData` or `ContainerLog`. It is not the Axone numeric `eventCategory` code. |
| Start time | `start` | UTC ISO-8601; inclusive. The UI converts the local date-time picker value to UTC. |
| End time | `end` | UTC ISO-8601; exclusive. It must be after `start`. |

Empty controls are omitted from the request; they must not become accidental
filters. The API returns a page shaped as:

```json
{
  "items": [],
  "total": 0,
  "limit": 100,
  "offset": 0
}
```

Use `limit` and `offset` for lazy loading or a **Load more** control. Do not
download the entire diagnostic dataset into the browser. SCRUM-53 owns the
server-side filtering, validation, ordering, and pagination.

## Real-log path and responsibilities

The browser must never call GE/Axone directly. The expected path is:

```text
Axone Log Export API (lab cluster only)
  -> SCRUM-43 acquisition and normalization
  -> Portrait IQ event store
  -> SCRUM-53 GET /api/logs
  -> SCRUM-54 filter controls and result view
```

### SCRUM-43: acquire and normalize

The existing Axone source integration is the approved product path for
acquiring data. In a lab deployment it uses the cluster-local Axone service,
then records acquisition metadata, stages the source data, deduplicates it,
and normalizes it before query. A frontend must use Portrait IQ's API, not a
manual `curl` request or a direct cluster IP.

Known Axone collection constraints are enforced by the acquisition layer:

- All-device requests use a range of no more than one hour, and both times
  must be in the same clock hour.
- Single-device requests may cover up to seven days.
- Source timestamps use the Axone format `YYYY-MM-DDTHH:MM:SS.mmm`; the lab
  integration treats them as UTC and normalizes stored events to UTC.
- Axone's optional `eventCategory` is a numeric source code. It is separate
  from the normalized `category` filter shown in this UI.
- Acquisition should use completed, settled windows; process windows
  sequentially, keep a watermark/manifest, and deduplicate retries. A future
  scheduler belongs in the backend or cluster, not in the browser.

### SCRUM-53: query normalized events

SCRUM-53 queries Portrait IQ's normalized event store. It does **not** fetch
from Axone at filter time. This keeps the response auditable, avoids exposing
lab network access to clients, and makes `limit`/`offset` pagination possible.

Normalized events include a UTC timestamp, severity, source, message, and
where available device ID, category, process, source file, and source line.
Not every source provides every optional field. Category and device identity
remain source-defined unless the backend explicitly maps them.

## Safe lab verification (read-only)

For a controlled lab check, first use read-only Kubernetes discovery from the
MiniEdge to confirm the Axone service and endpoint. A successful HTTP `404`
at the service root only proves that the service is reachable; it does not
prove that `/` is a valid API route. Use a documented endpoint through the
backend acquisition flow for actual collection.

If a mentor approves a small diagnostic sample, keep it in a private,
access-controlled local location. Verify only metadata such as file size,
row count, and the CSV header. Do not paste raw log rows into chat, commits,
issues, or pull requests. Never commit lab credentials, target addresses,
access tokens, or source exports.

## Integration checklist

1. Deploy/configure the backend source integration inside the lab cluster; do
   not make the browser access the internal Axone service.
2. Acquire an approved settled time window through the backend and confirm the
   normalized event-store count using safe metadata.
3. Connect this page's `toLogApiFilters()` output to authenticated
   `GET /api/logs`.
4. Render the returned page, preserving `total`, `limit`, and `offset` for
   lazy loading.
5. Show an empty state for no matches and a safe error state for unavailable
   data. Do not expose backend stack traces or raw source payloads.
6. Add integration tests for one filter at a time, combined filters, clear-one,
   clear-all, invalid time ranges, and pagination.

## Scope

This is a local-demo project integration plan, not GE SSO, production access
control, or a claim that a real GE environment is already connected. Any
production collection schedule, retention policy, access control, and audit
requirements must be agreed with GE and implemented by the backend/deployment
owners.
