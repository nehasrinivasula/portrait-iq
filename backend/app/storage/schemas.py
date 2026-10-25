"""Response models for /api/logs. Kept next to the code that fills them in
(the rest of the API's contracts live in app/schemas.py)."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


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
    events_expected: int
    events_in_store: int


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
