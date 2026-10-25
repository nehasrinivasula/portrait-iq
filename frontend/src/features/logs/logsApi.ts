import { apiFetch } from "../../api";
import { mockSearchEvents } from "./mockLogs";

// Shapes follow PR #10 (log-storage-unified-view): GET /api/events with a
// cursor. That PR is not merged, so the mock adapter is on by default; set
// VITE_LOGS_MOCK=false once /api/events exists. Check the field names then.

export type Severity = "DEBUG" | "INFO" | "WARN" | "ERROR";

export interface LogEvent {
  id: number;
  timestamp: string; // ISO, UTC
  device_id: string | null;
  source: string;
  category: string | null;
  severity: Severity;
  process: string | null;
  message: string;   // raw log text; may hold patient or staff data. Render as text only.
  source_file: string | null;
  source_line: number | null;
  job_id: string | null;
}

export interface LogPage {
  items: LogEvent[];
  next_cursor: string | null;
  total: number | null; // first page only
}

export interface EventQuery {
  cursor?: string | null;
  limit?: number;
  job_id?: string;
}

export const USE_MOCK = import.meta.env.VITE_LOGS_MOCK !== "false";

export function searchEvents(q: EventQuery = {}): Promise<LogPage> {
  if (USE_MOCK) return mockSearchEvents(q);
  const p = new URLSearchParams();
  p.set("limit", String(q.limit ?? 100));
  if (q.cursor) p.set("cursor", q.cursor);
  if (q.job_id) p.set("job_id", q.job_id);
  return apiFetch<LogPage>(`/api/events?${p}`);
}
