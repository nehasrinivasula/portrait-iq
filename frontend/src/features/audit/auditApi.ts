import { apiFetch } from "../../api";

// Audit timeline (SCRUM-36): GET /api/audit. Oldest first; times are UTC ISO.
// Descriptions are written by the server without passwords, tokens, log text or search values.

export interface AuditEntry {
  id: number;
  timestamp: string;
  actor: string;
  session_id: string | null;
  action: string;
  description: string;
}

export interface AuditPage {
  items: AuditEntry[];
  total: number;
  limit: number;
  offset: number;
}

export type AuditRange = "any" | "hour" | "day";

export interface AuditFilter {
  actor: string;
  action: string;
  sessionId: string;
  range: AuditRange;
}

export const emptyAuditFilter = (): AuditFilter => ({ actor: "", action: "", sessionId: "", range: "any" });
export const PAGE_SIZE = 50;

const RANGE_MS: Record<AuditRange, number | null> = { any: null, hour: 3_600_000, day: 86_400_000 };

/** Query string for a filter. Empty fields are left out; `now` is a parameter so tests are exact. */
export function auditParams(f: AuditFilter, limit: number, offset: number, now: Date = new Date()): URLSearchParams {
  const p = new URLSearchParams();
  if (f.actor.trim()) p.set("actor", f.actor.trim());
  if (f.action.trim()) p.set("action", f.action.trim());
  if (f.sessionId.trim()) p.set("session_id", f.sessionId.trim());
  const span = RANGE_MS[f.range];
  if (span !== null) p.set("since", new Date(now.getTime() - span).toISOString());
  p.set("limit", String(limit));
  p.set("offset", String(offset));
  return p;
}

/** Offset of the last page, so the newest entries show first (the server lists oldest first). */
export const latestOffset = (total: number, limit: number) => (total <= limit ? 0 : Math.floor((total - 1) / limit) * limit);

export const getAudit = (f: AuditFilter, limit: number, offset: number) =>
  apiFetch<AuditPage>(`/api/audit?${auditParams(f, limit, offset)}`);

const ACTION_LABELS: Record<string, string> = {
  login: "Sign in",
  session_check: "Session check",
  connect: "Connect to system",
  connectivity_check: "Connectivity check",
  job_start: "Start diagnostics",
  job_status: "Run status change",
  job_step: "Run step",
  acquire_logs: "Pull logs",
  source_test: "Test log source",
  upload: "Upload file",
  logs_search: "Search logs",
  logs_privacy_report: "PHI/PII report viewed",
  logs_privacy_scan: "PHI/PII scan",
  audit_view: "Audit timeline viewed",
  package_download: "Download package",
};

/** Readable name for an action; unknown ones are shown as stored. */
export const actionLabel = (a: string) => ACTION_LABELS[a] ?? a.replace(/_/g, " ");

/** A session id is long and random; the first 8 characters tell sessions apart. */
export const shortSession = (id: string | null) => (id ? id.slice(0, 8) : "—");
