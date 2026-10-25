"""
PLACEHOLDER for the compute-platform log format.

Michael Parrish (Service Engineering) said there are two log formats: the
Portrait side and the "compute logs". Andrew Welch showed a CSV downloaded
from the compute platform at a Finnish site. We have never seen a sample of
it, and we must not guess its columns — a parser written from imagination
would "pass" its own tests and then fail on the first real file.

sniff() therefore always returns False, so the registry never routes a file
here by accident. To implement it:

  1. Get a real export (ask Andrew Welch / the GE sponsor; scrubbed is fine).
  2. Profile its columns the way portrait_csv.py's were profiled.
  3. Fill in sniff() and parse() following portrait_csv.py as the template:
     build events ONLY with schema.make_event(), report every unconvertible
     row via report.reject(), and raise UnrecognizedLogFormat for file-level
     problems.
  4. Run `python -m app.ingestion.validate_samples <real samples>`; it must
     come back clean before this is considered done.

Note: the Portrait sample set already contains a few compute-style hosts
(e.g. hostName "um-pm-0.novalocal") alongside device MAC-address hosts, so
check whether those rows already ARE the "compute" data before assuming a
second file format exists.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from app.ingestion.parsers.base import LogParser, ParseReport
from app.ingestion.schema import NormalizedEvent


class ComputeCsvParser(LogParser):
    name = "compute_csv"
    source = "compute"

    def sniff(self, path: Path) -> bool:
        return False  # never claims a file until implemented against a real sample

    def parse(self, path: Path, report: Optional[ParseReport] = None) -> Iterator[NormalizedEvent]:
        raise NotImplementedError(
            "compute_csv needs a real sample of the compute-platform export first; "
            "see the module docstring."
        )


_PARSER = ComputeCsvParser()


def sniff(path: Path) -> bool:
    return _PARSER.sniff(Path(path))


def parse(path: Path, report: Optional[ParseReport] = None) -> Iterator[NormalizedEvent]:
    return _PARSER.parse(Path(path), report)
