import { apiFetch } from "../../api";

// Device discovery (D-3): POST /api/discover, GET /api/devices,
// PATCH /api/devices/{id} for the engineer's corrections (D-7).

export type DeviceKind = "hub" | "viewer" | "server" | "unknown";

export interface Device {
  id: string;
  name: string;
  kind: DeviceKind;
  ip_address: string;
  reachable: boolean;
  /** The engineer set the name or role; searches keep it. */
  overridden: boolean;
  /** Why the tool thinks this; only from a fresh search. */
  evidence?: string[];
}

export interface Discovery {
  system_ip: string;
  /** False: live capture is off here, so roles are unknown (ARP table only). */
  traffic_checked: boolean;
  devices: Device[];
  notes: string[];
}

export const KIND_LABELS: Record<DeviceKind, string> = {
  hub: "Hub", viewer: "Viewer", server: "System", unknown: "Unknown",
};

export const SEARCH_LENGTHS = [
  { s: 20, label: "Quick (20 sec)" },
  { s: 60, label: "Thorough (1 min)" },
] as const;

/** sweepSubnet: also check every address on the system's subnet (active scanning, opt-in). */
export const discover = (durationS: number, sweepSubnet = false) =>
  apiFetch<Discovery>("/api/discover", {
    method: "POST", body: JSON.stringify({ duration_s: durationS, sweep_subnet: sweepSubnet }),
  });

export const listDevices = () => apiFetch<Device[]>("/api/devices");

export const updateDevice = (id: string, patch: { name?: string; kind?: DeviceKind }) =>
  apiFetch<Device>(`/api/devices/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

const ORDER: Record<DeviceKind, number> = { hub: 0, viewer: 1, unknown: 2, server: 3 };

/** Hub first, then viewers, then the rest; by address within each. */
export function sortDevices(devices: Device[]): Device[] {
  const ip = (d: Device) => d.ip_address.split(".").map((n) => n.padStart(3, "0")).join(".");
  return [...devices].sort((a, b) => ORDER[a.kind] - ORDER[b.kind] || ip(a).localeCompare(ip(b)));
}
