"""Response models for /api/logs. Kept next to the code that fills them in
(the rest of the API's contracts live in app/schemas.py)."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class SensitiveFlagOut(BaseModel):
    """A span of a field that looks like PHI/PII. Never contains the text itself."""
    field: Literal["message", "device_id", "process", "category"]
    category: str                    # e.g. email, ssn, person_name (see GET /api/logs/privacy/categories)
    confidence: Literal["high", "medium", "low"]
    start: Optional[int]             # character range in the original field; null when the response is masked
    end: Optional[int]


class LogEventOut(BaseModel):
    id: int
    timestamp: datetime              # always UTC
    device_id: Optional[str]
    source: str
    category: Optional[str]
    severity: str
    process: Optional[str]
    message: str                     # raw log text: may contain patient or staff data
    source_file: Optional[str]
    source_line: Optional[int]
    job_id: Optional[str]
    privacy_scanned: bool = False    # false = not yet judged by the current scanner; treat as unchecked
    sensitive: list[SensitiveFlagOut] = []   # personal-data flags; empty + privacy_scanned = nothing found
    network_identifiers: list[SensitiveFlagOut] = []   # IP and MAC addresses; never masked, never "flagged"
    masked: bool = False             # true = flagged spans were replaced with [REDACTED:<category>]


class LogPageOut(BaseModel):
    items: list[LogEventOut]
    next_cursor: Optional[str]       # pass back as ?cursor= for the next page; null on the last page
    total: Optional[int]             # matching events overall; only on the first page


class FacetValueOut(BaseModel):
    value: Optional[str]             # null = events that have no value for this field
    count: int


class FacetsOut(BaseModel):
    total: int
    first_timestamp: Optional[datetime]
    last_timestamp: Optional[datetime]
    sources: list[FacetValueOut]
    severities: list[FacetValueOut]
    categories: list[FacetValueOut]
    devices: list[FacetValueOut]     # the 200 busiest
    distinct_devices: int


class CollectionOut(BaseModel):
    job_id: str                      # a job id, or "unassigned" for logs not tied to a collection
    status: Optional[str]
    mode: Optional[str]
    created_at: Optional[datetime]
    finished_at: Optional[datetime]
    events: int
    first_timestamp: Optional[datetime]
    last_timestamp: Optional[datetime]


class IssueOut(BaseModel):
    kind: str
    severity: Literal["problem", "info"]
    message: str
    file: Optional[str]


class FileSummaryOut(BaseModel):
    source_file: str
    parser: Optional[str]
    rows_read: int
    rows_rejected: int
    events_expected: int             # stored at ingest, minus any removed on purpose since
    events_in_store: int
    events_removed: int = 0


class CollectionSummaryOut(BaseModel):
    job_id: str
    status: Optional[str]
    mode: Optional[str]
    complete: bool                   # true only when issues has no "problem"
    events_stored: int
    events_expected: int
    rows_read: int
    rows_rejected: int
    first_timestamp: Optional[datetime]
    last_timestamp: Optional[datetime]
    by_source: list[FacetValueOut]
    by_severity: list[FacetValueOut]
    files: list[FileSummaryOut]
    issues: list[IssueOut]


class FlagCountOut(BaseModel):
    value: str
    count: int


class PrivacyFileOut(BaseModel):
    source_file: Optional[str]
    events: int
    flagged_events: int
    unscanned_events: int
    network_identifier_events: int = 0


class PrivacyReportOut(BaseModel):
    """What the PHI/PII scan found in one collection. Counts only, no log text."""
    job_id: str
    scanner_version: int
    events: int
    scanned_events: int
    unscanned_events: int            # not judged by the current scanner version
    all_scanned: bool                # events exist and every one was judged by the current scanner
    ingest_complete: Optional[bool] = None   # the collection's completeness (rejected rows, failed files); null for "all" and "unassigned"
    complete_for_packaging: bool = False     # all_scanned and not known to be incomplete: the check a package must pass
    flagged_events: int              # events with personal-data flags (IP and MAC addresses excluded)
    network_identifier_events: int   # events with IP or MAC addresses, counted apart
    network_by_category: list[FlagCountOut]
    by_category: list[FlagCountOut]
    by_confidence: list[FlagCountOut]
    by_field: list[FlagCountOut]
    files: list[PrivacyFileOut]
    limits: list[str]                # what the scan cannot find, to show next to the numbers


class PrivacyCategoryOut(BaseModel):
    category: str
    description: str
    group: Literal["personal", "network"]   # network kinds never make an event count as flagged


class RescanOut(BaseModel):
    scanned: int
