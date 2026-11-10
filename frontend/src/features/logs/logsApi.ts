import { apiFetch } from "../../api";
import { privacyParams, type PrivacyOptions, type SensitiveFlag } from "../privacy/privacyApi";

// Log search (PR #10): GET /api/logs/events, /facets. Times are UTC ISO.

export type Severity = "DEBUG" | "INFO" | "WARN" | "ERROR";

export interface LogEvent {
  id: number;
  timestamp: string;
  device_id: string | null;
  source: string;
  category: string | null;
  severity: Severity;
  process: string | null;
  message: string;   // raw log text; may hold patient or staff data. Render as text only.
  source_file: string | null;
  source_line: number | null;
  job_id: string | null;
  // PHI/PII scan (S-3). Absent from older servers; flags carry kinds and positions, never the matched text.
  privacy_scanned?: boolean;
  sensitive?: SensitiveFlag[];
  masked?: boolean;
}

export interface LogPage {
  items: LogEvent[];
  next_cursor: string | null;
  total: number | null; // first page only
}

export interface FacetValue { value: string | null; count: number }
export interface Facets {
  total: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  sources: FacetValue[];
  severities: FacetValue[];
  categories: FacetValue[];
  devices: FacetValue[];
  distinct_devices: number;
}

/** Column the field-value filter applies to. */
export type FieldColumn = "device" | "category" | "process";

export interface LogFilter {
  since: string | null;
  until: string | null;
  sources: string[];
  severities: Severity[];
  column: FieldColumn;
  value: string;
  q: string;
  jobId: string | null;
}

export const emptyFilter = (): LogFilter => ({
  since: null, until: null, sources: [], severities: [], column: "device", value: "", q: "", jobId: null,
});

/** Query string for the filters. Empty values are left out. */
export function filterParams(f: LogFilter): URLSearchParams {
  const p = new URLSearchParams();
  if (f.since) p.set("since", f.since);
  if (f.until) p.set("until", f.until);
  f.sources.forEach((s) => p.append("source", s));
  f.severities.forEach((s) => p.append("severity", s));
  const v = f.value.trim();
  if (v) p.set(f.column === "process" ? "process" : f.column, v);
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.jobId) p.set("job_id", f.jobId);
  return p;
}

export function searchEvents(f: LogFilter, cursor: string | null, limit = 100, privacy?: PrivacyOptions): Promise<LogPage> {
  const p = filterParams(f);
  privacyParams(privacy).forEach((v, k) => p.set(k, v));
  p.set("limit", String(limit));
  if (cursor) p.set("cursor", cursor);
  return apiFetch<LogPage>(`/api/logs/events?${p}`);
}

/** With mask on, the server leaves out device and category values that were flagged. */
export function getFacets(f: LogFilter, mask = false) {
  const p = filterParams(f);
  if (mask) p.set("mask", "true");
  return apiFetch<Facets>(`/api/logs/facets?${p}`);
}

export interface AcquisitionResult {
  run_id: string;
  source: string;
  new_files: number;
  already_acquired: number;
  failed: number;
  over_budget: number;
  files: { name: string; status: string; events?: number | null; rows_rejected?: number | null; error?: string | null }[];
}

export function importLogFiles(files: File[]) {
  const body = new FormData();
  files.forEach((f) => body.append("files", f));
  return apiFetch<AcquisitionResult>("/api/sources/local/upload", { method: "POST", body });
}

export interface Source { id: string; display_name: string; configured: boolean; reason: string }
export const listSources = () => apiFetch<Source[]>("/api/sources");

/** Pull a window from a source and ingest it under a job, so the logs belong to that run. */
export const acquireForJob = (sourceId: string, jobId: string, since: string, until: string) =>
  apiFetch<AcquisitionResult>(`/api/sources/${sourceId}/acquire`, {
    method: "POST",
    body: JSON.stringify({ since, until, job_id: jobId, ingest: true }),
  });
