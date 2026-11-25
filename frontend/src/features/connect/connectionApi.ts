import { apiFetch } from "../../api";

export const CONNECTION_PORTS = [22, 443] as const;

export interface ConnectivityPortResult {
  port: number;
  status: string;
  message: string;
  latency_ms?: number | null;
}

export interface ConnectivityResult {
  target: string;
  resolved_addresses: string[];
  status: "reachable" | "partially_reachable" | "unreachable";
  reachable: boolean;
  summary: string;
  results: ConnectivityPortResult[];
}

export interface ConnectionSettings {
  namespace: string;
  node_ips: string[];
  interfaces: string[];
  dds_domain_ids: number[];
}

export interface ConnectionSetupResult {
  mode: "live" | "pre_install";
  ip_address: string;
  connectivity: ConnectivityResult;
  settings: ConnectionSettings;
  notes: string[];
}

export interface ConnectionInput {
  ip_address: string;
  username: string;
  password: string;
  mode?: "live" | "pre_install";
}

export function checkConnection(target: string) {
  return apiFetch<ConnectivityResult>("/api/connectivity-check", {
    method: "POST",
    body: JSON.stringify({ target, ports: CONNECTION_PORTS, timeout_seconds: 3 }),
  });
}

export function detectConnection(input: ConnectionInput) {
  return apiFetch<ConnectionSetupResult>("/api/connect", {
    method: "POST",
    body: JSON.stringify({ ...input, mode: input.mode ?? "live" }),
  });
}
