"""
Run the PHI/PII scanner over real sample logs WITHOUT a database and print
counts only. Use it to see what the scanner finds in a log set, and how long
it takes, before trusting it.

    python -m app.privacy.scan_samples path/to/samples.zip
    python -m app.privacy.scan_samples path/to/folder_of_logs -v

Prints, per file and in total: events scanned, events flagged, flags by kind
and by confidence. It never prints log text or matched values, so the output is
safe to paste into a chat or a ticket. Exit code is 0 when every file was
scanned, 1 when a file could not be read.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from app.ingestion.parsers.base import ParseReport, UnrecognizedLogFormat
from app.ingestion.registry import detect_parser, iter_events
from app.ingestion.validate_samples import _collect_files
from app.privacy.scanner import SCANNER_VERSION, scan_event_fields


def _shape(text: str) -> str:
    """Hide the characters, keep the form: digits -> 9, letters -> a / A."""
    return "".join("9" if c.isdigit() else ("a" if c.islower() else ("A" if c.isupper() else c)) for c in text)


def scan_file(path: Path, examples: Optional[Counter] = None) -> Optional[tuple[int, int, Counter, Counter]]:
    parser = detect_parser(path)
    if parser is None:
        return None
    events = flagged = 0
    kinds: Counter = Counter()
    confidences: Counter = Counter()
    for e in iter_events(path, parser=parser, report=ParseReport()):
        events += 1
        flags = scan_event_fields(message=e.message, device_id=e.device_id, process=e.process, category=e.category)
        if flags:
            flagged += 1
            for f in flags:
                kinds[f.category] += 1
                confidences[f.confidence] += 1
                if examples is not None and f.field == "message":
                    before = e.message[max(0, f.start - 24):f.start]
                    examples[(f.category, f"{_shape(before)}<{_shape(e.message[f.start:f.end])}>")] += 1
    return events, flagged, kinds, confidences


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Count PHI/PII flags in sample log files (counts only, no text).")
    ap.add_argument("inputs", nargs="+", type=Path, help="files, folders or .zip archives")
    ap.add_argument("-v", "--verbose", action="store_true", help="one line per file")
    ap.add_argument("--examples", type=int, metavar="N", default=0,
                    help="show the N most common SHAPES per kind: 24 characters before each match and the match "
                         "itself with every digit shown as 9 and letters as a/A, so no real value appears")
    args = ap.parse_args(argv)

    problems = 0
    total_events = total_flagged = 0
    kinds: Counter = Counter()
    confidences: Counter = Counter()
    examples: Optional[Counter] = Counter() if args.examples else None
    start = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        files = _collect_files(args.inputs, Path(tmp))
        if not files:
            print("No .csv/.log/.txt files found in the given inputs.", file=sys.stderr)
            return 1
        for path in files:
            try:
                result = scan_file(path, examples)
            except (UnrecognizedLogFormat, NotImplementedError, OSError) as exc:
                result, problems = None, problems + 1
                print(f"[FAIL] {path.name}: {type(exc).__name__}")
                continue
            if result is None:
                problems += 1
                print(f"[FAIL] {path.name}: no registered parser recognises this file")
                continue
            n, fl, k, c = result
            total_events += n
            total_flagged += fl
            kinds.update(k)
            confidences.update(c)
            if args.verbose:
                print(f"[ok]   {path.name}: events={n:,} flagged={fl:,}")
    seconds = time.perf_counter() - start

    print(f"\n=== PHI/PII scan (scanner v{SCANNER_VERSION}) ===")
    print(f"files={len(files)} events={total_events:,} flagged_events={total_flagged:,} seconds={seconds:.1f}")
    for kind, n in kinds.most_common():
        print(f"  {kind}: {n:,}")
    if confidences:
        print(f"  by confidence: {dict(confidences)}")
    if examples:
        for kind in kinds:
            print(f"\n  shapes for {kind}:")
            for (k, shape), n in [x for x in examples.most_common() if x[0][0] == kind][:args.examples]:
                print(f"    {n:>8,}  {shape}")
    print("Counts and shapes only: no log text or values were printed.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
