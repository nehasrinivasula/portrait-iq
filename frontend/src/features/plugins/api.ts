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

export type JobStatus = "queued" | "running" | "done" | "failed" | "cleanup_failed" | "interrupted";

export interface Job {
  id: string;
  mode: string;
  status: JobStatus;
  current_step: string | null;
  created_at: string;
  finished_at: string | null;
}

export type ToolResult =
  | { tool: string; summary: string; warnings: string[]; artifact_paths: string[] }
  | { tool: string; error: string };

export const listPlugins = () => apiFetch<PluginList>("/api/plugins");

export const startJob = (targetIp: string, tools: string[], pluginOptions: PluginOptions) =>
  apiFetch<Job>(`/api/jobs?target_ip=${encodeURIComponent(targetIp)}`, {
    method: "POST",
    body: JSON.stringify({ tools, plugin_options: pluginOptions }),
  });

export const getJob = (id: string) => apiFetch<Job>(`/api/jobs/${id}`);
export const getResults = (id: string) => apiFetch<ToolResult[]>(`/api/jobs/${id}/results`);

export async function uploadPcap(file: File) {
  const body = new FormData();
  body.append("file", file);
  return apiFetch<{ upload_id: string; kind: string; size_bytes: number }>("/api/uploads?kind=pcap", {
    method: "POST",
    body,
  });
}

export const isFinished = (s: JobStatus) => s !== "queued" && s !== "running";
