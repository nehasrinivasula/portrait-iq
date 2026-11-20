# Where PHI/PII can enter the diagnostic workflow (S-3)

This is the one list of every point where patient or staff data can reach Portrait IQ, what the app does about
it, and what is still open. It replaces the scattered "PHI/PII collection point" notes in the backend README.
**Keep it current:** a new log source or tool plugin is not finished until it has a row here
(`backend/tests/test_phi_collection_points.py` fails when a registered one is missing).

Never commit real exports, captures or packages (`.gitignore` blocks `/data/`, `*.zip`, `*.pcap*`).

## How to read the "what we do" column

| Word | Meaning |
|---|---|
| **Scan** | Looked at with `privacy.scanner` and counted. The report holds counts and positions, never the matched text. |
| **Mask** | Flagged text replaced with `[REDACTED:<kind>]` in the Logs page and in an export (on by default). Personal kinds only; IP and MAC addresses are kept because equipment addresses are what the tools are for. |
| **Exclude** | Left out, or never collected. |
| **Protect** | Stored owner-only in a gitignored folder, never written to the audit log. |

Scanner version, rules and limits: [PHI/PII flagging](../backend/README.md#phipii-flagging-s-3). It matches patterns
(labelled values, HL7 segments, SSNs, emails, phones, card numbers, street addresses); it cannot recognise free
text such as a bare name, so a clean scan is "no known pattern found", not "no PHI".

## Collection points

| # | Collection point | How it enters | What it can hold | What we do | What remains |
|---|---|---|---|---|---|
| 1 | **Device logs from the Axone export API** (`sources/axone_export.py`) | Pulled by window, saved as CSV, ingested | `description` text with patient names, MRNs, DOB, HL7 segments; device and process ids | **Scan** every event on ingest (all text fields); **mask** in the Logs page and export; **protect** the staged CSVs (staging folder, gitignored); the audit log records counts and ids only | Pattern-based, so free-text names can pass |
| 2 | **Device logs from Box** (`sources/box.py`) | Pulled by modified time, same flow as above | Same as row 1 (raw exports) | Same as row 1 | Same as row 1. Box credentials are secrets, not PHI, and are redacted from logs |
| 3 | **Local import and browser uploads of logs** (`sources/local_import.py`, `POST /api/sources/local/upload`) incl. hub logs pulled by hand (`collectors/hub_import.py`) | Files or ZIPs chosen by the engineer | Same as row 1; **file names** can name a patient | **Scan** and **mask** as row 1. Names are not trusted: upload folders use random ids, and file names are not written to the audit log. Extension, ZIP and size checks. Local imports are deduplicated by content hash | Local imports are not time-filtered, so everything in a chosen file is kept. The hub import plugin itself is still a stub |
| 4 | **Packet captures: pcap payloads** (`collectors/tshark.py`, run or import mode) | Live capture from a tool pod, or a capture hospital IT made, uploaded with `POST /api/uploads?kind=pcap` | Anything on the wire; even headers-only keeps the first bytes of each packet | **Exclude** payload by default: headers only (`snaplen`), and full payload needs `full_payload_reason`, which is audited. **Protect** the capture (owner-only, random upload id, never logged). A masked export **excludes** the capture file, because binary cannot be masked. The tshark text summary is **scanned** (row 5) | A capture cannot be scanned. If an engineer exports unmasked, the capture goes in the zip and the report lists it as "not scanned". Headers-only lowers the risk, it does not remove it. Live capture is refused unless enabled for an approved site |
| 5 | **Text the tools produce: Netshoot and tshark output** (`netshoot.txt/json`, tshark `summary.txt`, and the summary and warnings in `tools/results.json`) | Written to the run folder, copied into the package | Hostnames, addresses, command output; tshark conversation tables | **Scan** every line of every text file with `scan_text`, masked or not, and report counts per file in `privacy/report.json` under `tools`. **Mask** personal kinds when masking is on. Secrets are redacted before output is stored | Text over 50 MB, non-UTF-8 text and binary files are not scanned (listed in the report, with a warning when one is in the zip). IP and MAC addresses are counted but not masked |
| 6 | **RTI / UMF DDS discovery** (`collectors/rti.py`) | `rtiddsspy` in a short-lived pod | **Samples** (message contents) can carry patient data | **Exclude** sample contents: `-printSample` is never passed and the parser keeps only topic and type names. The `rti.txt` raw output and `rti.json` are **scanned** like row 5 | Not yet checked against a real bench. Unrecognised lines stay in the raw output, which is why it is scanned. Topic names could in principle carry identifiers |
| 7 | **Kubernetes container logs** (`ingestion/parsers/k8s_log.py`, files from `kubectl logs --timestamps`) | Imported like any log (row 3) | Application log text from the `pm` namespace | Same as row 1 (scan, mask) | Parser not yet validated on a real MiniEdge log |
| 8 | **Kubernetes events and diagnostics** (cluster events, pod descriptions, node data) | *Planned collector, not built yet* | Pod and object names, annotations, event messages (which echo log text), environment values | Plan: collect **only** what the checks need, **scan** all text with `scan_text` before it is stored or packaged, **mask** like row 5, and **exclude** secrets, ConfigMap and Secret values and environment variables | Add the final rows here, and a test, when the collector lands (see below) |
| 9 | **Grafana data** (dashboard queries, panel data, health: the `collect_grafana_health` step) | *Planned collector, not built yet; the step is a stub today* | Panel series labels and annotations can carry device, patient or location names | Plan: store health and metric **counts** rather than raw panel data where possible; **scan** and **mask** whatever text is kept like row 5; **exclude** dashboards' stored credentials and data-source settings | Add the final rows here when the collector lands |
| 10 | **Analysis findings** (`analysis/`) | Derived from logs | Evidence text copied from log events | Findings store event ids and counts. Evidence and examples are **masked** when shown and in the package | Any new field that copies log text must be scanned too |
| 11 | **Audit log** (`audit.py`) | Written by the app | Whatever is put in `detail` | **Exclude** by rule: ids, counts, kinds and states only; never log text, file names or values (tests check this) | Free-text inputs (a run's reason, a device label) are validated but could hold anything the engineer types |
| 12 | **The export package** (`packaging/`) | Built on request | All of the above | One report: `privacy/report.json` covers log events and tool text; a masked export leaves out files it cannot mask; warnings in the package and the Export page say when masking was off or a file was not scanned | The zip leaves the product when downloaded: handling it is the engineer's and the site's responsibility |

## What `privacy/report.json` says

- Top level: log events scanned or not, flagged events, counts by kind, `complete_for_packaging` (everything
  stored was scanned and nothing failed to load).
- `tools`: for every text file and for `results.json`, lines scanned, lines with personal data, lines with
  network identifiers, counts by kind. Each file also says whether it was included and, if not scanned, why.
  `tools.complete` is true only when every file in the zip was read by the scanner. If the tools step failed
  the section says `scanned: false`; a report never claims a scan that did not happen.
- Never any matched text.

## Adding a collection point

1. Add a row above: how it enters, what it can hold, scan/mask/exclude, what remains.
2. Route its text through `scan_text` (events through ingestion, tool files through the package's tool step) and
   its binary content through the exclusion rule. Decide what a masked export does with it.
3. Keep it out of the audit log and out of log lines (ids and counts only).
4. Add the collector or source id to the doc (the test needs its registry name to appear in this file).
