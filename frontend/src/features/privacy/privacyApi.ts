import { apiFetch } from "../../api";

// PHI/PII flagging (S-3): GET/POST /api/logs/collections/{id}/privacy[/scan] and, for every collection, /api/logs/privacy[/scan].
// The report holds counts only, never log text.

export const UNASSIGNED = "unassigned";

export interface FlagCount { value: string; count: number }

export interface PrivacyFile {
  source_file: string | null;
  events: number;
  flagged_events: number;
  unscanned_events: number;
  network_identifier_events: number;
}

export interface PrivacyReport {
  job_id: string;
  scanner_version: number;
  events: number;
  scanned_events: number;
  unscanned_events: number;
  all_scanned: boolean;
  /** false: some files or rows of the collection did not load. Absent on older servers. */
  ingest_complete?: boolean | null;
  complete_for_packaging?: boolean;
  flagged_events: number;
  network_identifier_events: number;
  network_by_category: FlagCount[];
  by_category: FlagCount[];
  by_confidence: FlagCount[];
  by_field: FlagCount[];
  files: PrivacyFile[];
  limits: string[];
}

/** One flag on an event: where it is and what kind. Never the matched text. */
export interface SensitiveFlag {
  field: string;
  category: string;
  confidence: "high" | "medium" | "low";
  start: number | null;
  end: number | null;
}

/** The two switches above the log table. */
export interface PrivacyOptions { flaggedOnly: boolean; mask: boolean }
export const noPrivacyOptions = (): PrivacyOptions => ({ flaggedOnly: false, mask: false });

/** Query parameters for the options. Off switches are left out. */
export function privacyParams(o: PrivacyOptions | undefined): URLSearchParams {
  const p = new URLSearchParams();
  if (o?.flaggedOnly) p.set("flagged", "true");
  if (o?.mask) p.set("mask", "true");
  return p;
}

const LABELS: Record<string, string> = {
  person_name: "Person name",
  record_identifier: "Patient / record number",
  date_of_birth: "Date of birth",
  hl7_patient_segment: "HL7 patient segment",
  ssn: "Social Security number",
  email: "Email address",
  phone: "Phone number",
  credit_card: "Payment card",
  street_address: "Street address",
  ip_address: "IP address",
  mac_address: "MAC address",
};

/** Readable name for a scanner category; unknown ones are shown as they come. */
export const categoryLabel = (c: string) => LABELS[c] ?? c.replace(/_/g, " ");

const plural = (n: number, one: string, many: string) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** One sentence for the top of the panel. */
export function privacyHeadline(r: PrivacyReport): string {
  if (r.events === 0) return "No logs in this collection yet.";
  if (!r.all_scanned) return `${plural(r.unscanned_events, "event has", "events have")} not been scanned yet.`;
  if (r.ingest_complete === false)
    return r.flagged_events === 0
      ? "Scanned, but some logs did not load, so this can't be called clean."
      : `Scanned: ${plural(r.flagged_events, "event", "events")} may contain PHI/PII, and some logs did not load.`;
  if (r.flagged_events === 0) return "Scanned: no known PHI/PII patterns found.";
  return `Scanned: ${plural(r.flagged_events, "event", "events")} may contain PHI/PII.`;
}

/** Key of the report for every collection, shown when no single run is selected. */
export const ALL_LOGS = "all";

export interface PrivacyView {
  key: string;            // the run id, or ALL_LOGS: lets the panel ignore a report for something it no longer shows
  report: PrivacyReport;
}

const scope = (jobId: string | null) => (jobId ? `/api/logs/collections/${encodeURIComponent(jobId)}/privacy` : "/api/logs/privacy");

/** The report for what the table shows: one run, or the server's total over every collection. */
export async function loadPrivacy(jobId: string | null): Promise<PrivacyView> {
  return { key: jobId ?? ALL_LOGS, report: await apiFetch<PrivacyReport>(scope(jobId)) };
}

/** Scan whatever in this scope has not been scanned. */
export const scanScope = (jobId: string | null) =>
  apiFetch<{ scanned: number }>(`${scope(jobId)}/scan`, { method: "POST" });

/** Panel colour for a report: only a fully scanned, fully loaded, flag-free one is green. */
export function privacyKind(r: PrivacyReport): "ok" | "warn" {
  return r.all_scanned && r.ingest_complete !== false && r.flagged_events === 0 ? "ok" : "warn";
}
