# Plugin endpoints (SCRUM-39)

| What it does | Method and path | Body | Returns |
|---|---|---|---|
| List the plugins | `GET /api/plugins` | none | `{ plugins: [{ name, display_name, description }], load_errors }` |
| Upload a capture (tshark import only) | `POST /api/uploads?kind=pcap` | multipart form, field `file` (.pcap or .pcapng, up to 500 MB) | `{ upload_id, kind, size_bytes }` |
| Start a job | `POST /api/jobs?target_ip=<ip>` | JSON: `{ tools: [...], plugin_options: {...} }` | the job, with `status: "queued"` |
| Job status | `GET /api/jobs/{id}` | none | `{ id, status, current_step, created_at, finished_at }` |
| Job results | `GET /api/jobs/{id}/results` | none | one entry per tool: `{ tool, summary, warnings, artifact_paths }` or `{ tool, error }` |

## tshark live capture filters

A live capture (`"mode": "run"`) needs exactly one of these in its options:

| Option | Value | Captures |
|---|---|---|
| `filter_preset` | `rti_all` | All UMF RTI traffic (DDS domains 7, 12, 13) |
| `filter_preset` | `rti_logging` | RTI logging only (domain 7) |
| `filter_preset` | `rti_source_pod` | RTI source pod only (domain 12) |
| `filter_preset` | `rti_view_pod` | RTI view pod only (domain 13) |
| `capture_filter` | BPF text, e.g. `host 192.0.2.1` | Anything else (up to 200 characters: letters, digits, spaces and `. : / ( ) ! = < > & \| [ ] -`) |

Example: `{ "tools": ["tshark"], "plugin_options": { "tshark": { "mode": "run", "filter_preset": "rti_all", "duration_s": 60 } } }`
