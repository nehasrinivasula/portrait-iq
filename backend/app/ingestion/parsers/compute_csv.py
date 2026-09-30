"""
Stub. Michael Parrish and Andrew Welch both independently said there are
two log formats: this is the "compute platform" side (as opposed to
Portrait's own logs, handled by portrait_csv.py). We have never seen a
sample of it.

Before writing this for real:
  1. Get a sample export from Andrew Welch or the service engineering team.
  2. Profile it the way portrait_csv.py's columns were profiled — don't
     guess the schema.
  3. Implement sniff() and parse() with the same signatures as
     portrait_csv.py so pipeline.py can pick between them without change.
"""
from collections.abc import Iterator
from pathlib import Path

from app.ingestion.schema import NormalizedEvent


def sniff(path: Path) -> bool:
    return False  # never matches until this is implemented


def parse(path: Path) -> Iterator[NormalizedEvent]:
    raise NotImplementedError("Need a real compute-platform log sample before writing this.")
