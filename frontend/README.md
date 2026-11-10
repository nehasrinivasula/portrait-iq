# Portrait IQ frontend

React/Vite UI for Portrait IQ. From `frontend/`:

```bash
npm install
npm run dev
```

The dev server proxies `/api` to the backend (`API_TARGET`, see
`vite.config.ts`). Sign in with the login configured in the backend's
environment; never put those values in this repository.

## Diagnostic runs after logout or idle lock (SCRUM-94)

Starting a diagnostic creates a server-side job and returns immediately. The
job continues in the backend after the browser signs out or its session locks;
it does not depend on the bearer token that started it. In the same browser
session, Portrait IQ retains only the opaque job ID (not the token) and, after
the engineer signs in again, restores that job's status, progress, and results
on the **Run diagnostics** page.

The browser displays **Restoring your previous diagnostic run…** while it
checks the saved ID. The engineer can explicitly clear a finished run.

This local-demo behavior does not survive a backend/pod restart: an unfinished
job is marked `interrupted` for safe cleanup. Closing the browser may also
remove session storage; a future multi-user deployment should add a
server-side "recent jobs" list with owner authorization.

## Log filters (SCRUM-54)

The Logs page (`src/features/logs/`) filters stored, normalized events with
`GET /api/logs/events` and fills its pick lists from `GET /api/logs/facets`
(backend `app/storage/routes.py`).

| Control | Query parameter | Notes |
| --- | --- | --- |
| Date | `since`, `until` | UTC ISO 8601. `since` is inclusive, `until` exclusive. "Any time" sends neither. |
| Source | `source` | Repeated, one per chip picked. |
| Level | `severity` | `DEBUG`, `INFO`, `WARN`, `ERROR`; repeated. |
| Column equals | `device`, `category` or `process` | Exact match on the chosen column. |
| Search messages | `q` | Case-insensitive text in the message. |
| Run | `job_id` | Set when the page is opened from a run. |

Every filter is combined with AND, and empty controls are left out of the
request. Each applied filter shows as a chip under the controls; its ×
removes just that filter, and **Clear filters** removes them all. Results
page with `limit` and `next_cursor`, so the browser never downloads the
whole store.

### Time-frame presets and calendar view (SCRUM-92)

The **Date** control is a drop-down so a field engineer can choose a common
window without typing timestamps:

- Any time (stored-log search only)
- Last hour, last 6 hours, last 24 hours, or last 7 days
- Custom range

Choosing **Custom range…** opens native **From** and **To** calendar/time
controls. The UI validates that both values are present, neither is in the
future, and the end is after the start. It displays local time for the
engineer, then sends UTC ISO 8601 values to the API.

The same picker is reused on the **Run** page before a log pull. A new pull is
limited to the prior 30 days, while a Logs-page search can use any stored
history. Selecting a date range filters the Portrait IQ event store; it does
not contact GE/Axone from the browser or retrieve data at filter time.

## Where the logs come from

The browser never calls GE/Axone directly:

```text
Axone Log Export API (inside the lab cluster)
  -> backend acquisition and normalization (SCRUM-43)
  -> Portrait IQ event store
  -> GET /api/logs/events
  -> Logs page
```

Filtering reads the event store; it never pulls from Axone at filter time.
Not every source fills every field (device, category and process can be
empty).

## Lab data

Log messages can hold patient or staff data. Show them as text only, and
never paste raw rows, captures, lab logins, target addresses or tokens into
commits, issues, pull requests or chat.
