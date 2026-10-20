"""
Parser for the "all device log" CSV format shown to us by Michael Parrish
and Andrew Welch, and profiled from the 35 sample files you uploaded.

Real columns, confirmed from the sample data:
  timeStampAndOffset, processCounter, concentrationCounter, logLevel,
  hostName, sourceUUID, friendlyName, processInfo, sourceInfo,
  eventCategory, description

This is the one parser in the skeleton that's actually been run against
real data rather than sketched from a description. It has NOT been tested
against a live export from your lab or from a different site — do that
before trusting it fully.
"""
import re
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from app.ingestion.schema import NormalizedEvent

# "axone_logger[777:0]" (sample exports) and "axone-log-export:4521:main"
# (log export API) both -> the bare process name.
_PROC_STRIP = re.compile(r"\[.*|:\d+:.*$")


def sniff(path: Path) -> bool:
    """Return True if this file looks like a Portrait all-device-log CSV.
    Cheap header check — good enough to route files in pipeline.py."""
    try:
        header = pd.read_csv(path, nrows=0).columns.tolist()
    except Exception:
        return False
    required = {"timeStampAndOffset", "logLevel", "hostName", "eventCategory", "description"}
    return required.issubset(set(header))


def parse(path: Path, chunksize: int = 200_000) -> Iterator[NormalizedEvent]:
    """Yields NormalizedEvent objects. Streams in chunks — these files run
    ~12MB / ~16k rows each in the sample set, but don't assume that holds
    at every site; chunking keeps memory flat regardless."""
    for chunk in pd.read_csv(
        path, chunksize=chunksize, dtype=str, keep_default_na=False, on_bad_lines="skip"
    ):
        for row in chunk.itertuples(index=False):
            ts = pd.to_datetime(row.timeStampAndOffset, utc=True, errors="coerce")
            if pd.isna(ts):
                continue  # a small number of rows are malformed in the sample data
            device_id = row.friendlyName if row.friendlyName != "unsetFriendlyName" else row.hostName
            yield NormalizedEvent(
                timestamp=ts.to_pydatetime(),
                device_id=device_id,
                source="portrait",
                category=row.eventCategory or None,
                severity=(row.logLevel or "INFO").upper(),
                process=_PROC_STRIP.sub("", row.processInfo) if row.processInfo else None,
                message=row.description,
            )
