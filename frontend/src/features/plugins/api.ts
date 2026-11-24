import { apiFetch } from "../../api";

// Contract: docs/plugins-api.md (SCRUM-39).

export interface Plugin {
  name: string;
  display_name: string;
  description: string;
}

export interface PluginList {
  plugins: Plugin[];
  load_errors: Record<string, string>;
}

export type PluginOptions = Record<string, Record<string, unknown>>;

export interface JobConfiguration {
  namespace: string;
  allowed_namespaces: string[];
  cpu_limit_pct: number;
  mem_limit_mb: number;
  max_output_mb: number;
  tshark: { duration_s: number; interface: string; filter_preset: string };
  rti: { domain: number; duration_s: number };
  netshoot: { ports: number[]; dns_names: string[]; extra_targets: string[] };
}

export type JobStatus = "queued" | "running" | "done" | "partial" | "failed" | "cleanup_failed" | "interrupted";

// Why a run failed (SCRUM-68): the step, the tool involved, and a plain reason.
export interface RunError {
  step: string | null;
  plugin: string | null;
  reason: string;
}

export interface Job {
  id: string;
  mode: string;
  status: JobStatus;
  current_step: string | null;
  tools?: string[];            // the tools selected for this run, in run order
  configuration?: Record<string, unknown> | null;
  skipped_steps?: string[];   // steps the selected tools never use
  step_times?: Record<string, [string | null, string | null]>;   // step -> [started, finished], naive UTC
  error: RunError | null;   // set only on a failed/interrupted run
  created_at: string;
  finished_at: string | null;
}

export type ToolResult =
  | { tool: string; summary: string; warnings: string[]; artifact_paths: string[]; details?: Record<string, unknown> }
  | { tool: string; step?: string; error: string };

export const listPlugins = () => apiFetch<PluginList>("/api/plugins");
export const getJobConfiguration = () => apiFetch<JobConfiguration>("/api/jobs/configuration");

export const startJob = (targetIp: string, tools: string[], pluginOptions: PluginOptions,
  configuration?: { namespace: string; cpu_limit_pct: number; mem_limit_mb: number; max_output_mb: number; overridden_fields: string[] }) =>
  apiFetch<Job>(`/api/jobs?target_ip=${encodeURIComponent(targetIp)}`, {
    method: "POST",
    body: JSON.stringify({ tools, plugin_options: pluginOptions, ...configuration }),
  });

export const getJob = (id: string) => apiFetch<Job>(`/api/jobs/${id}`);
export const getResults = (id: string) => apiFetch<ToolResult[]>(`/api/jobs/${id}/results`);

export async function uploadFile(file: File, kind: string) {
  const body = new FormData();
  body.append("file", file);
  return apiFetch<{ upload_id: string; kind: string; size_bytes: number }>(
    `/api/uploads?kind=${encodeURIComponent(kind)}`,
    { method: "POST", body },
  );
}

export const isFinished = (s: JobStatus) => s !== "queued" && s !== "running";
