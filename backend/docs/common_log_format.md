# Common log format

Every log source is converted into one shape, `NormalizedEvent`
(`app/ingestion/schema.py`), before anything else in the platform sees it.
Event store, analysis rules, knowledge lookup, analytics and packaging only
ever read this shape; none of them should know or care which source an event
came from.

## The shape

| Field         | Type            | Guarantee |
|---------------|-----------------|-----------|
| `timestamp`   | `datetime`      | Timezone-aware, always **UTC**. Naive timestamps are rejected, never guessed. |
| `severity`    | `str`           | Exactly one of `DEBUG`, `INFO`, `WARN`, `ERROR`. `CRITICAL`/`FATAL`/etc. map to `ERROR`. |
| `source`      | `str`           | Lowercase slug for the kind of source: `portrait`, `kubernetes`, `compute`, ... |
| `message`     | `str`           | Always a string (may be empty), never `None`. |
| `device_id`   | `str \| None`   | Whatever identifies the device *in that source*. Blank becomes `None`. |
| `category`    | `str \| None`   | Source-defined vocabulary (e.g. `General`, `PerfData`, `ContainerLog`). Not unified across sources. |
| `process`     | `str \| None`   | Emitting process, when the source has one. |
| `source_file` | `str \| None`   | Raw file the event came from. |
| `source_line` | `int \| None`   | Line in that file (first line of the record for multi-line CSV rows). |

In the database (`normalized_events`) timestamps are stored as **naive UTC**
because SQLite drops timezone info. `ingestion/pipeline.py` converts before
writing; never write a non-UTC time to that column.

## Using it

```python
from app.ingestion.registry import iter_events
from app.ingestion.parsers.base import ParseReport

report = ParseReport()
for event in iter_events(path, report=report):   # auto-detects the format
    ...
print(report.rows_read, report.events_parsed, report.rows_rejected)
```

Storing into the event store (atomic and idempotent per file):

```python
from app.ingestion.pipeline import ingest_file
result = ingest_file(path, db, job_id=job.id)   # raises on failure, stores nothing
```

`ingest_file` returns only if the whole file was stored; it raises
`UnrecognizedLogFormat` or `IngestError` otherwise. A caller that records
"processed files" can mark a file processed exactly when it returns.

## Nothing is silently lost

Every row a parser reads becomes an event **or** a counted rejection with a
reason (`ParseReport.reject_reasons`). `ingest_file` fails a file if more than
1% of its rows were rejected. Unknown severity strings are kept as `INFO` and
recorded in `ParseReport.warnings`.

## Adding a new source

1. Subclass `LogParser` (`parsers/base.py`): implement `sniff()` (cheap
   check, must not raise) and `parse()` (a generator).
2. Build every event with `make_event()` — never construct `NormalizedEvent`
   directly — so UTC/severity/blank-handling rules apply.
3. Report every row you can't convert with `report.reject(line, reason, excerpt)`;
   raise `UnrecognizedLogFormat` for whole-file problems.
4. Add an instance to `PARSERS` in `registry.py`.
5. Add an entry to `FIXTURES` in `tests/test_parser_contract.py`. A parser
   without one fails the suite on purpose.
6. **Get a real sample and run the validator (below).** A parser is not
   validated until it passes on a real file of its type.

## Validation status

| Source type | Parser | Validated against real samples? |
|---|---|---|
| Portrait "all device logs" CSV | `portrait_csv` | **Yes.** 35 files, 548,303 rows: all parsed, 0 rejected; results cross-checked against an independent pandas analysis. |
| Kubernetes pod logs (`kubectl logs --timestamps`) | `k8s_log` | **No.** Built from the documented format; synthetic tests only. |
| Compute-platform export | `compute_csv` | **No. Placeholder.** Never claims a file; needs a real sample. |

Run the validator on any real sample (needs no database or network):

```bash
python -m app.ingestion.validate_samples path/to/samples.zip
python -m app.ingestion.validate_samples pod.log --parser k8s_log
```

Exit code 0 means every file was recognised, every row accounted for, and the
reject rate was under 1%.

### Getting real samples

* **Kubernetes:** the lab MiniEdge (192.168.0.3) is a real k8s node, so no GE
  permission is needed:
  `kubectl logs --timestamps -n pm <pod> > <pod>.log`
  (the lab's namespace is `pm`; list pods with `kubectl get pods -n pm`).
  Collect logs BEFORE scaling any deployment down: once a pod is deleted,
  `kubectl logs` can no longer read it.
  Name the file after the pod (the parser uses the file name as `device_id`).
* **Compute platform:** ask Andrew Welch / the GE sponsor for a scrubbed
  export. First check whether the `um-pm-0.novalocal` rows already present in
  the Portrait sample *are* the compute data.

**Never commit real or scrubbed GE logs to the repo.** `.gitignore` excludes
`/data/` and `*.zip`; keep samples there or outside the repo.

## Known limitations (deliberate, not oversights)

* **Device identity is per-source.** A Portrait `hostName`, a pod name and an
  IP are three different `device_id`s even for the same machine. Correlating
  across sources (e.g. hub log vs. Wireshark capture) needs a device registry.
* **`category` is not unified** across sources.
* **Kubernetes severity is a heuristic.** kubectl output has no level field;
  severity is inferred from klog prefixes, `level=` keys, or a leading
  uppercase token, and defaults to `INFO`.
* **Timestamps are only as good as device clocks.** A device with an unsynced
  clock produces correctly-formatted but wrong times. Not detected here.
* **`PerfData` is kept** (about two thirds of rows) because usage analytics
  will need it; filter by `category` when you don't.
