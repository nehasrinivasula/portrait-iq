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
| 5 | **Text the tools produce: Netshoot and tshark output** (`netshoot.txt/json`, tshark `summary.txt`, and the summary and warnings in `tools/results.json`); also any tool's text artifact, e.g. the Grafana snapshot (row 9) | Written to the run folder, copied into the package | Hostnames, addresses, command output; tshark conversation tables | **Scan** every line of every text file with `scan_text`, masked or not, and report counts per file in `privacy/report.json` under `tools`. **Mask** personal kinds when masking is on. Secrets are redacted before output is stored | Text over 50 MB, non-UTF-8 text and binary files are not scanned (listed in the report, with a warning when one is in the zip). IP and MAC addresses are counted but not masked |
| 6 | **RTI / UMF DDS discovery** (`collectors/rti.py`) | `rtiddsspy` in a short-lived pod | **Samples** (message contents) can carry patient data | **Exclude** sample contents: `-printSample` is never passed and the parser keeps only topic and type names. The `rti.txt` raw output and `rti.json` are **scanned** like row 5 | Not yet checked against a real bench. Unrecognised lines stay in the raw output, which is why it is scanned. Topic names could in principle carry identifiers |
| 7 | **Kubernetes container logs** (`ingestion/parsers/k8s_log.py`, files from `kubectl logs --timestamps`) | Imported like any log (row 3) | Application log text from the `pm` namespace | Same as row 1 (scan, mask) | Parser not yet validated on a real MiniEdge log |
| 8 | **Kubernetes events and diagnostics** (cluster events, pod descriptions, node data) | *Planned collector, not built yet* | Pod and object names, annotations, event messages (which echo log text), environment values | Plan: collect **only** what the checks need, **scan** all text with `scan_text` before it is stored or packaged, **mask** like row 5, and **exclude** secrets, ConfigMap and Secret values and environment variables | Add the final rows here, and a test, when the collector lands (see below) |
| 9 | **Grafana / system health** (`collectors/grafana.py`, PR #52, open) | Read-only snapshot through Grafana's HTTP API: Grafana and data-source health, node CPU / memory / disk, dashboard inventory, firing alerts. The full JSON is a job artifact | Alert label sets, dashboard titles and annotations can carry device, room or site names | The UI summary shows **counts and levels only**. The JSON artifact goes through the tool-output scan (row 5): **scanned** and **masked** (personal kinds) in the package, counted in `privacy/report.json` under `tools`. No tool pod; bounded by timeouts and a size cap | Raw panel data and the Prometheus databases are not collected here (row 15). The scan is pattern-based, so a bare site name can pass |
| 10 | **Analysis findings** (`analysis/`) | Derived from logs | Evidence text copied from log events | Findings store event ids and counts. Evidence and examples are **masked** when shown and in the package | Any new field that copies log text must be scanned too |
| 11 | **Audit log** (`audit.py`) | Written by the app | Whatever is put in `detail` | **Exclude** by rule: ids, counts, kinds and states only; never log text, file names or values (tests check this) | Free-text inputs (a run's reason, a device label) are validated but could hold anything the engineer types |
| 12 | **System folder: Log Export CSVs and CMS log snaps** (`system/`, `sources/cms_client.py`) | The export step copies Log Export CSVs already pulled (row 1) and downloads the newest existing CMS snap (`GET /cms/sprsnap/download/...`, a tar). It never generates or deletes a snap | CSVs: as row 1. A snap: any log the CMS service writes | CSVs are **scanned** line by line and **masked**, and counted in `privacy/report.json` under `system`. A snap is a tar: it cannot be scanned or masked, so a masked export **excludes** it and an unmasked export includes it with a warning, listed as "not scanned" | The inside of a tar is never scanned (extracting and scanning its text files is a follow-up). Whether generating a snap for the run's window is allowed on the live CMS is a question for the system owners. The CMS response format and any authentication are not verified |
| 13 | **RTI Monitor `*.ser` and AdminConsole `*.xml`** (listed in `system/manifest.json`, no collector yet) | *Not built.* Exported from the live RTI host | A `.ser` is a recording, so it can hold message **samples** (patient data); the XML holds discovery details: hosts, participants, topics | Plan: treat `.ser` like a pcap (binary, **excluded** from a masked export, opt-in with a reason and audited); the XML is text, so **scan** and **mask** it like row 5 | Not collected. Decide with the mentors whether a `.ser` may be collected at all |
| 14 | **Core dump files** (listed in `system/manifest.json`, no collector yet) | *Not built.* Read from the core dump folder on the node(s) | A process memory dump can contain anything, including patient data and credentials | Plan: **exclude** by default; if ever included, opt-in with an audited reason, never in a masked export. Binary, so never scanned | The highest-risk item on the list. Not collected, and its path on the system is not known yet |
| 15 | **Prometheus databases** (`collectors/prometheus_tsdb.py`, PR #52, open; also listed in `system/manifest.json`) | A compressed archive (`prometheus_tsdb.tar.zst` or `.tar.gz`) of the TSDB blocks, written to the run folder by a short-lived root pod | Metric label values (host, pod, device and site names) | A compressed archive is binary, so it **cannot be scanned or masked**: a masked export **leaves it out** and says so, an unmasked export includes it with a warning, and `privacy/report.json` lists it under "tools" as not scanned. Prefer the counts the Grafana collector already reads (row 9) | Contents of the archive are never read by the scanner; include it only in an unmasked export |
| 16 | **Firewall settings** (listed in `system/manifest.json`, no collector yet) | *Not built.* Printed on the node(s), e.g. `firewall-cmd --list-all` | Text with IP addresses, ports and zones | Plan: text, so **scan** and **mask** like row 5 (IPs and ports are network identifiers, kept) | Not collected. Running it over SSH from the backend would need a key stored in the app, which is a security decision |
| 17 | **The export package** (`packaging/`) | Built on request | All of the above | One report: `privacy/report.json` covers log events and tool text; a masked export leaves out files it cannot mask; warnings in the package and the Export page say when masking was off or a file was not scanned | The zip leaves the product when downloaded: handling it is the engineer's and the site's responsibility |

## What `privacy/report.json` says

- Top level: log events scanned or not, flagged events, counts by kind, `complete_for_packaging` (everything
  stored was scanned and nothing failed to load).
- `tools` and `system`: for every text file (and, for `tools`, `results.json`), lines scanned, lines with
  personal data, lines with network identifiers, counts by kind. Each file also says whether it was included and,
  if not scanned, why. `complete` is true only when every file in the zip was read by the scanner. If a step failed
  its section says `scanned: false`; a report never claims a scan that did not happen.
- Never any matched text.

## Adding a collection point

1. Add a row above: how it enters, what it can hold, scan/mask/exclude, what remains.
2. Route its text through `scan_text` (events through ingestion, tool files through the package's tool step) and
   its binary content through the exclusion rule. Decide what a masked export does with it.
3. Keep it out of the audit log and out of log lines (ids and counts only).
4. Add the collector or source id to the doc (the test needs its registry name to appear in this file).
