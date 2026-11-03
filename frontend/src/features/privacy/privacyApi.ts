import { apiFetch } from "../../api";

// PHI/PII flagging (S-3): GET/POST /api/logs/collections/{id}/privacy[/scan].
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
  if (r.flagged_events === 0) return "Scanned: no known PHI/PII patterns found.";
  return `Scanned: ${plural(r.flagged_events, "event", "events")} may contain PHI/PII.`;
}

/** Key of the combined report shown when no single run is selected. */
export const ALL_LOGS = "all";

export interface PrivacyView {
  key: string;            // the run id, or ALL_LOGS: lets the panel ignore a report for something it no longer shows
  report: PrivacyReport;
  toScan: string[];       // collections that still hold unscanned events
}

function mergeCounts(lists: FlagCount[][]): FlagCount[] {
  const total = new Map<string, number>();
  lists.flat().forEach((c) => total.set(c.value, (total.get(c.value) ?? 0) + c.count));
  return [...total].map(([value, count]) => ({ value, count })).sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
}

/** One report for several collections: counts add up, and it is only "all scanned" if every part is. */
export function combineReports(reports: PrivacyReport[], key: string): PrivacyReport {
  const sum = (pick: (r: PrivacyReport) => number) => reports.reduce((n, r) => n + pick(r), 0);
  const unscanned = sum((r) => r.unscanned_events);
  const events = sum((r) => r.events);
  return {
    job_id: key,
    scanner_version: reports[0]?.scanner_version ?? 0,
    events,
    scanned_events: events - unscanned,
    unscanned_events: unscanned,
    all_scanned: unscanned === 0,
    flagged_events: sum((r) => r.flagged_events),
    network_identifier_events: sum((r) => r.network_identifier_events),
    network_by_category: mergeCounts(reports.map((r) => r.network_by_category)),
    by_category: mergeCounts(reports.map((r) => r.by_category)),
    by_confidence: mergeCounts(reports.map((r) => r.by_confidence)),
    by_field: mergeCounts(reports.map((r) => r.by_field)),
    files: reports.flatMap((r) => r.files),
    limits: reports[0]?.limits ?? [],
  };
}

export const getPrivacyReport = (jobId: string) =>
  apiFetch<PrivacyReport>(`/api/logs/collections/${encodeURIComponent(jobId)}/privacy`);

export const scanCollection = (jobId: string) =>
  apiFetch<{ scanned: number }>(`/api/logs/collections/${encodeURIComponent(jobId)}/privacy/scan`, { method: "POST" });

interface CollectionInfo { job_id: string; events: number }
const listCollections = () => apiFetch<CollectionInfo[]>("/api/logs/collections?limit=200");

/** The report for what the table shows: one run, or every collection (including logs not tied to a run). */
export async function loadPrivacy(jobId: string | null): Promise<PrivacyView> {
  if (jobId) {
    const report = await getPrivacyReport(jobId);
    return { key: jobId, report, toScan: report.unscanned_events > 0 ? [jobId] : [] };
  }
  const reports = await Promise.all((await listCollections()).filter((c) => c.events > 0).map((c) => getPrivacyReport(c.job_id)));
  return {
    key: ALL_LOGS,
    report: combineReports(reports, ALL_LOGS),
    toScan: reports.filter((r) => r.unscanned_events > 0).map((r) => r.job_id),
  };
}

export async function scanAll(ids: string[]): Promise<void> {
  for (const id of ids) await scanCollection(id);
}
