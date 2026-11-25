// Pure helpers for the Run page: validation (D-9) and building the
// plugin_options payload. Limits mirror backend/app/collectors/*.py.

export { validateTargetIp } from "../../validation";

/** Each live tool's length limits (tshark.py: 5 to 900 s; rti.py: 10 to 300 s). */
export interface DurationLimits { min: number; max: number }
export const TSHARK_DURATION: DurationLimits = { min: 5, max: 900 };
export const RTI_DURATION: DurationLimits = { min: 10, max: 300 };
export const DURATION_PRESETS = [
  { s: 30, label: "30 sec" },
  { s: 60, label: "1 min" },
  { s: 300, label: "5 min" },
  { s: 900, label: "15 min" },
] as const;
export const TSHARK_DEFAULT_S = 60;
export const RTI_DEFAULT_S = 30;
export const RTI_DOMAINS = [7, 12, 13] as const;

/** Prometheus TSDB export range: the whole database, or a recent window. */
export const TSDB_SCOPES = [
  { id: "full", label: "Full database" },
  { id: "7d", label: "Last 7 days" },
  { id: "24h", label: "Last 24 hours" },
] as const;
export type TsdbScope = (typeof TSDB_SCOPES)[number]["id"];
export const TSDB_DEFAULT_SCOPE: TsdbScope = "full";

/** Named capture filters the backend expands (docs/plugins-api.md). */
export const TSHARK_PRESETS = [
  { id: "rti_all", label: "All UMF RTI traffic", hint: "Domains 7, 12 and 13" },
  { id: "rti_logging", label: "RTI logging", hint: "Domain 7" },
  { id: "rti_source_pod", label: "RTI source pod", hint: "Domain 12" },
  { id: "rti_view_pod", label: "RTI view pod", hint: "Domain 13" },
] as const;
export type TsharkPresetId = (typeof TSHARK_PRESETS)[number]["id"];

export interface TsharkChoice {
  mode: "run" | "import";
  durationS: string;
  uploadId: string | null;
  /** quick = a named preset, manual = a port the engineer types. */
  filterKind: "quick" | "manual";
  preset: TsharkPresetId;
  port: string;
  proto: "any" | "tcp" | "udp";
  interfaceName: string;
}
export interface RtiChoice {
  domain: number;
  durationS: string;
}
export interface Choices {
  tshark: TsharkChoice;
  rti: RtiChoice;
  netshoot: { ports: string; dnsNames: string; extraTargets: string };
  prometheus_tsdb: { scope: TsdbScope };
}

export const defaultChoices = (): Choices => ({
  tshark: {
    mode: "run", durationS: String(TSHARK_DEFAULT_S), uploadId: null,
    filterKind: "quick", preset: "rti_all", port: "", proto: "any", interfaceName: "any",
  },
  rti: { domain: 12, durationS: String(RTI_DEFAULT_S) },
  netshoot: { ports: "22, 443, 6443", dnsNames: "kubernetes.default.svc.cluster.local, axone-log-export-tcp.pm.svc.cluster.local", extraTargets: "" },
  prometheus_tsdb: { scope: TSDB_DEFAULT_SCOPE },
});

const splitList = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
const HOST = /^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$/;
const validHost = (name: string) => {
  if (HOST.test(name)) return true;
  try { return new URL(`http://[${name}]/`).hostname.startsWith("["); }
  catch { return false; }
};

const wholeSeconds = (v: string, lim: DurationLimits): string | null => {
  const n = Number(v);
  return v.trim() !== "" && Number.isInteger(n) && n >= lim.min && n <= lim.max
    ? null
    : `Enter a whole number of seconds from ${lim.min} to ${lim.max}.`;
};

const validPort = (v: string): boolean => {
  const n = Number(v);
  return v.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= 65535;
};

/** One message per field problem, keyed by field id, for the selected tools only. */
export function validateChoices(selected: Set<string>, c: Choices): Record<string, string> {
  const errs: Record<string, string> = {};
  if (selected.has("tshark")) {
    if (c.tshark.mode === "run") {
      const e = wholeSeconds(c.tshark.durationS, TSHARK_DURATION);
      if (e) errs["tshark.duration"] = e;
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$/.test(c.tshark.interfaceName))
        errs["tshark.interface"] = "Enter an interface name such as any or eth0.";
      if (c.tshark.filterKind === "manual" && !validPort(c.tshark.port))
        errs["tshark.port"] = "Enter a port number from 1 to 65535.";
    } else if (!c.tshark.uploadId) {
      errs["tshark.upload"] = "Choose a .pcap or .pcapng capture to import.";
    }
  }
  if (selected.has("rti")) {
    const e = wholeSeconds(c.rti.durationS, RTI_DURATION);
    if (e) errs["rti.duration"] = e;
  }
  if (selected.has("netshoot")) {
    const ports = splitList(c.netshoot.ports);
    if (ports.length > 20 || ports.some((port) => !validPort(port)))
      errs["netshoot.ports"] = "Use up to 20 comma-separated ports from 1 to 65535.";
    for (const [key, value] of [["dnsNames", c.netshoot.dnsNames], ["extraTargets", c.netshoot.extraTargets]]) {
      const names = splitList(value);
      if (names.length > 20 || names.some((name) => !validHost(name)))
        errs[`netshoot.${key}`] = "Use up to 20 comma-separated host names or IP addresses.";
    }
  }
  return errs;
}

/** Headers-only stays on (the backend default); the UI never offers full payload. */
export function buildOptions(selected: Set<string>, c: Choices): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  if (selected.has("tshark")) {
    const t = c.tshark;
    if (t.mode === "import") out.tshark = { mode: "import", upload_id: t.uploadId };
    else {
      const filter =
        t.filterKind === "quick"
          ? { filter_preset: t.preset }
          : { capture_filter: `${t.proto === "any" ? "" : `${t.proto} `}port ${Number(t.port)}` };
      out.tshark = { mode: "run", duration_s: Number(t.durationS), interface: t.interfaceName, ...filter };
    }
  }
  if (selected.has("rti")) out.rti = { domain: c.rti.domain, duration_s: Number(c.rti.durationS) };
  if (selected.has("prometheus_tsdb")) out.prometheus_tsdb = { scope: c.prometheus_tsdb.scope };
  if (selected.has("netshoot")) out.netshoot = {
    ports: splitList(c.netshoot.ports).map(Number), dns_names: splitList(c.netshoot.dnsNames),
    extra_targets: splitList(c.netshoot.extraTargets),
  };
  return out;
}
