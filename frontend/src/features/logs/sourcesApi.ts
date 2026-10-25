import { apiFetch } from "../../api";

export interface Source {
  id: string;
  display_name: string;
  needs_network: boolean;
  configured: boolean;
  reason: string;
}
export interface RemoteFile {
  remote_id: string;
  name: string;
  size: number | null;
  modified_at: string | null;
  already_acquired: boolean;
}

export const listSources = () => apiFetch<Source[]>("/api/sources");
export const previewFiles = (id: string, since: string, until: string) =>
  apiFetch<RemoteFile[]>(`/api/sources/${id}/files?since=${encodeURIComponent(since)}&until=${encodeURIComponent(until)}`);
