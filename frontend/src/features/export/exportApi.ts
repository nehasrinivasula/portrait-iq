import { apiDownload, apiFetch } from "../../api";

// Contract: backend/app/packaging/routes.py (SCRUM-61).

export type StepStatus = "pending" | "running" | "done" | "warn" | "failed";
export type PackageState = "preparing" | "ready" | "failed" | "interrupted";

export interface ExportIssue { item: string; reason: string }

export interface ExportStep {
  id: string;
  label: string;
  status: StepStatus;
  detail: string | null;
  issues: ExportIssue[];
}

export interface ContentEntry {
  path: string;
  description: string;
  files: number;
  events: number | null;
  masked: boolean | null;
}

export interface Package {
  job_id: string;
  state: PackageState;
  masked: boolean;
  partial: boolean;
  requested_by: string;
  started_at: string;
  finished_at: string | null;
  steps: ExportStep[];
  file_name: string | null;
  size_bytes: number | null;
  sha256: string | null;
  contents: ContentEntry[];
  warnings: string[];
  error: string | null;
  downloadable: boolean;
}

export type ExportScope = "run" | "imported" | "all";

export interface Preview {
  job_id: string;
  scope: ExportScope;
  title: string;               // "Run", "Imported logs" or "All logs"
  job_status: string | null;   // null unless a run
  finished: boolean;
  events: number;
  tools: { tool: string; ok: boolean; files: number; error: string | null }[];
  findings: number;
  privacy: {
    complete_for_packaging: boolean;
    all_scanned: boolean;
    ingest_complete: boolean;
    flagged_events: number;
    unscanned_events: number;
  };
  warnings: string[];
  steps: ExportStep[];
  package: Package | null;
}

const base = (jobId: string) => `/api/jobs/${encodeURIComponent(jobId)}/package`;

export const getPreview = (jobId: string) => apiFetch<Preview>(`${base(jobId)}/preview`);
export const getPackage = (jobId: string) => apiFetch<Package>(base(jobId));
export const startExport = (jobId: string, mask: boolean) =>
  apiFetch<Package>(base(jobId), { method: "POST", body: JSON.stringify({ mask }) });

/** Fetch the zip with the session's token and hand it to the browser as a download. */
export async function downloadPackage(jobId: string, fallbackName: string): Promise<string> {
  const { blob, fileName } = await apiDownload(`${base(jobId)}/download`);
  const name = fileName ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return name;
}
