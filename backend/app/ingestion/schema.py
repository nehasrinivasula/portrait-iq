"""
NormalizedEvent is the one shape every parser must produce, regardless of
source format. This is the fix for the complaint every stakeholder raised
in a different way: two log formats (Portrait vs. compute platform) that
never get joined, and per-device files that have to be stitched together
by hand.

Add a new source by writing a new parser that yields these — never by
changing what the analysis engine or knowledge layer expects to read.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class NormalizedEvent:
    timestamp: datetime
    device_id: Optional[str]     # hostName / friendlyName from the source
    source: str                    # "portrait" | "compute" | "network"
    category: Optional[str]          # e.g. "General", "PerfData", "UserAction"
    severity: str                     # "INFO" | "WARN" | "ERROR"
    process: Optional[str]
    message: str
