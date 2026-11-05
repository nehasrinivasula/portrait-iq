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

export interface LogFilter {
  since: string | null;
  until: string | null;
  sources: string[];
  severities: Severity[];
  device: string;          // the machine that wrote the entry; "" for any
  category: string;        // the kind of event, as the system labels it; "" for any
  process: string;         // the program that wrote it; "" for any
  sourceFile: string;      // one log file; "" for any
  q: string;
  jobId: string | null;    // a run, "unassigned" for imported logs, or null for all
}

export const emptyFilter = (): LogFilter => ({
  since: null, until: null, sources: [], severities: [], device: "", category: "", process: "", sourceFile: "",
  q: "", jobId: null,
});

/** Query string for the filters. Empty values are left out. */
export function filterParams(f: LogFilter): URLSearchParams {
  const p = new URLSearchParams();
  if (f.since) p.set("since", f.since);
  if (f.until) p.set("until", f.until);
  f.sources.forEach((s) => p.append("source", s));
  f.severities.forEach((s) => p.append("severity", s));
  if (f.device.trim()) p.set("device", f.device.trim());
  if (f.category.trim()) p.set("category", f.category.trim());
  if (f.process.trim()) p.set("process", f.process.trim());
  if (f.sourceFile) p.set("source_file", f.sourceFile);
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
  files: { name: string; status: string; events?: number | null; error?: string | null }[];
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

// --- Log sets: what runs and imports exist on this machine ---------------------

/** "unassigned" in the API: logs imported without a run. */
export const IMPORTED = "unassigned";

export interface LogSet {
  job_id: string;            // a run id, or IMPORTED
  status: string | null;
  mode: string | null;
  created_at: string | null;
  finished_at: string | null;
  events: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  tools: string[];
}

export interface LogSetFile { source_file: string; parser: string | null; events_in_store: number }

export const listLogSets = () => apiFetch<LogSet[]>("/api/logs/collections?limit=200");
export const getLogSetFiles = (id: string) =>
  apiFetch<{ files: LogSetFile[] }>(`/api/logs/collections/${encodeURIComponent(id)}`).then((s) => s.files);
