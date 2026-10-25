# Plugin endpoints (SCRUM-39)

| What it does | Method and path | Body | Returns |
|---|---|---|---|
| List the plugins | `GET /api/plugins` | none | `{ plugins: [{ name, display_name, description }], load_errors }` |
| Upload a capture (tshark import only) | `POST /api/uploads?kind=pcap` | multipart form, field `file` (.pcap or .pcapng, up to 500 MB) | `{ upload_id, kind, size_bytes }` |
| Start a job | `POST /api/jobs?target_ip=<ip>` | JSON: `{ tools: [...], plugin_options: {...} }` | the job, with `status: "queued"` |
| Job status | `GET /api/jobs/{id}` | none | `{ id, status, current_step, created_at, finished_at }` |
| Job results | `GET /api/jobs/{id}/results` | none | one entry per tool: `{ tool, summary, warnings, artifact_paths }` or `{ tool, error }` |
