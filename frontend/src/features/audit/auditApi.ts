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
  since: string | null;     // start time worked out once, when the filter is applied (see withRange)
}

export const emptyAuditFilter = (): AuditFilter => ({ actor: "", action: "", sessionId: "", range: "any", since: null });
export const PAGE_SIZE = 50;

const RANGE_MS: Record<AuditRange, number | null> = { any: null, hour: 3_600_000, day: 86_400_000 };

/** Fix "last hour" / "last 24 hours" to an exact start time. Done once on Apply so paging doesn't shift it. */
export function withRange(f: AuditFilter, now: Date = new Date()): AuditFilter {
  const span = RANGE_MS[f.range];
  return { ...f, since: span === null ? null : new Date(now.getTime() - span).toISOString() };
}

/** Query string for a filter. Empty fields are left out. */
export function auditParams(f: AuditFilter, limit: number, offset: number): URLSearchParams {
  const p = new URLSearchParams();
  if (f.actor.trim()) p.set("actor", f.actor.trim());
  if (f.action.trim()) p.set("action", f.action.trim());
  if (f.sessionId.trim()) p.set("session_id", f.sessionId.trim());
  if (f.since) p.set("since", f.since);
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
  logs_facets_view: "Log filters viewed",
  logs_privacy_report: "PHI/PII report viewed",
  logs_privacy_scan_start: "PHI/PII scan started",
  logs_privacy_scan: "PHI/PII scan",
  collection_view: "Collection viewed",
  collections_view: "Collections list viewed",
  device_add: "Add device",
  device_update: "Update device",
  devices_view: "Devices viewed",
  discover: "Find devices",
  source_devices_view: "Source devices viewed",
  sources_view: "Log sources viewed",
  job_view: "Run viewed",
  job_results_view: "Run results viewed",
  findings_view: "Findings viewed",
  job_interrupted: "Run interrupted",
  plugin_cleanup_failed: "Diagnostic cleanup failed",
  tool_pods_swept: "Leftover diagnostics removed",
  audit_view: "Audit timeline viewed",
  package_download: "Download package",
};

/** Every action the page can filter by, as [code, label], in label order. */
export const ACTION_OPTIONS: [string, string][] = Object.entries(ACTION_LABELS).sort((a, b) => a[1].localeCompare(b[1]));

/** Readable name for an action; unknown ones are shown as stored. */
export const actionLabel = (a: string) => ACTION_LABELS[a] ?? a.replace(/_/g, " ");

/** A session id is long and random; the first 8 characters tell sessions apart. */
export const shortSession = (id: string | null) => (id ? id.slice(0, 8) : "—");
