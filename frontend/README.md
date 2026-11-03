# Portrait IQ frontend

React/Vite UI for Portrait IQ. From `frontend/`:

```bash
npm install
npm run dev
```

The dev server proxies `/api` to the backend (`API_TARGET`, see
`vite.config.ts`). Sign in with the login configured in the backend's
environment; never put those values in this repository.

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
