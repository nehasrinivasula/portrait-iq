"""
Validate the common log format against REAL sample files.

    python -m app.ingestion.validate_samples path/to/samples.zip
    python -m app.ingestion.validate_samples path/to/folder_of_logs
    python -m app.ingestion.validate_samples some.log --parser k8s_log

For every file it reports which parser claimed it, how many rows were read,
converted and rejected (and why), the severity mix, the devices seen and the
time span. Exit code is 0 only if EVERY file was recognised, every row was
accounted for, and no file exceeded --max-reject-rate; otherwise 1 — so it
can gate a CI job or a pre-push check.

This needs no database and no network. Run it whenever a new kind of sample
arrives (a compute-platform export, a kubectl log from the lab MiniEdge,
a scrubbed customer file). A parser has not been validated against a source
type until this passes on a real file of that type.

Sample files can contain sensitive data. Nothing here writes log content
anywhere; rejected-row excerpts are printed to the console only, truncated.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.ingestion.parsers.base import ParseReport, UnrecognizedLogFormat
from app.ingestion.registry import detect_parser, get_parser, iter_events

SAMPLE_SUFFIXES = {".csv", ".log", ".txt"}


@dataclass
class FileValidation:
    file: str
    parser: Optional[str]
    passed: bool
    problem: Optional[str]
    report: Optional[ParseReport]


def validate_file(path: Path, parser_name: Optional[str], max_reject_rate: float) -> FileValidation:
    parser = get_parser(parser_name) if parser_name else detect_parser(path)
    if parser is None:
        return FileValidation(path.name, None, False, "no registered parser recognises this file", None)
    report = ParseReport()
    try:
        for _ in iter_events(path, parser=parser, report=report):
            pass
    except UnrecognizedLogFormat as exc:
        return FileValidation(path.name, parser.name, False, str(exc), report)
    except NotImplementedError as exc:
        return FileValidation(path.name, parser.name, False, f"parser not implemented: {exc}", report)

    if report.rows_read == 0:
        return FileValidation(path.name, parser.name, False, "no log rows found", report)
    if not report.accounted_for:
        return FileValidation(
            path.name, parser.name, False,
            f"row accounting broken: read {report.rows_read} != parsed {report.events_parsed} + rejected {report.rows_rejected}",
            report,
        )
    if report.reject_rate > max_reject_rate:
        return FileValidation(
            path.name, parser.name, False,
            f"reject rate {report.reject_rate:.2%} exceeds {max_reject_rate:.2%}", report,
        )
    return FileValidation(path.name, parser.name, True, None, report)


def _collect_files(inputs: list[Path], workdir: Path) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        if item.is_dir():
            files += sorted(p for p in item.rglob("*") if p.suffix.lower() in SAMPLE_SUFFIXES)
        elif item.suffix.lower() == ".zip":
            dest = workdir / item.stem
            with zipfile.ZipFile(item) as zf:
                for member in zf.namelist():
                    if Path(member).suffix.lower() in SAMPLE_SUFFIXES and not member.endswith("/"):
                        zf.extract(member, dest)  # extract() strips absolute paths and '..'
            files += sorted(p for p in dest.rglob("*") if p.suffix.lower() in SAMPLE_SUFFIXES)
        elif item.is_file():
            files.append(item)
        else:
            raise FileNotFoundError(item)
    return files


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Validate log parsers against real sample files.")
    ap.add_argument("inputs", nargs="+", type=Path, help="files, folders or .zip archives")
    ap.add_argument("--parser", help="force a parser by name instead of auto-detecting")
    ap.add_argument("--max-reject-rate", type=float, default=0.01)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("-v", "--verbose", action="store_true", help="show every file, not just problems")
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        files = _collect_files(args.inputs, Path(tmp))
        if not files:
            print("No .csv/.log/.txt files found in the given inputs.", file=sys.stderr)
            return 1
        results = [validate_file(f, args.parser, args.max_reject_rate) for f in files]

    failed = [r for r in results if not r.passed]
    if args.json:
        print(json.dumps([
            {
                "file": r.file, "parser": r.parser, "passed": r.passed, "problem": r.problem,
                **({} if r.report is None else {
                    "rows_read": r.report.rows_read, "events": r.report.events_parsed,
                    "rejected": r.report.rows_rejected, "reject_reasons": dict(r.report.reject_reasons),
                    "warnings": dict(r.report.warnings), "severity": dict(r.report.severity_counts),
                    "devices": len(r.report.devices),
                }),
            } for r in results
        ], indent=2))
        return 1 if failed else 0

    by_parser: dict[str, list[FileValidation]] = {}
    for r in results:
        by_parser.setdefault(r.parser or "(unrecognised)", []).append(r)

    for r in results:
        if r.passed and not args.verbose:
            continue
        status = "PASS" if r.passed else "FAIL"
        rep = r.report
        detail = f"rows={rep.rows_read:,} events={rep.events_parsed:,} rejected={rep.rows_rejected}" if rep else ""
        print(f"[{status}] {r.file}  parser={r.parser}  {detail}")
        if r.problem:
            print(f"        problem: {r.problem}")
        if rep and rep.reject_reasons:
            print(f"        reject reasons: {dict(rep.reject_reasons)}")
            for s in rep.reject_samples[:3]:
                print(f"          line {s.line}: {s.reason}: {s.excerpt!r}")
        if rep and rep.warnings:
            print(f"        warnings: {dict(rep.warnings)}")

    print("\n=== summary ===")
    for parser_name, group in by_parser.items():
        reports = [g.report for g in group if g.report]
        rows = sum(x.rows_read for x in reports)
        events = sum(x.events_parsed for x in reports)
        rejected = sum(x.rows_rejected for x in reports)
        sev: dict[str, int] = {}
        devices: set = set()
        firsts = [x.first_timestamp for x in reports if x.first_timestamp]
        lasts = [x.last_timestamp for x in reports if x.last_timestamp]
        for x in reports:
            for k, v in x.severity_counts.items():
                sev[k] = sev.get(k, 0) + v
            devices |= x.devices
        ok = sum(1 for g in group if g.passed)
        print(f"{parser_name}: {ok}/{len(group)} files passed | rows={rows:,} events={events:,} rejected={rejected:,}")
        if events:
            print(f"    severity={sev} devices={len(devices)}")
            print(f"    span={min(firsts).isoformat()} -> {max(lasts).isoformat()} (UTC)")
    print(f"\n{'ALL FILES PASSED' if not failed else f'{len(failed)} OF {len(results)} FILES FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
