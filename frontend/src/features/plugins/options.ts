// Pure helpers for the Run page: validation (D-9) and building the
// plugin_options payload. Limits mirror backend/app/collectors/*.py.

const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
export const validateTargetIp = (v: string): string | null =>
  !v.trim() ? "Enter the IP address of the Portrait Mobile server."
  : IPV4.test(v.trim()) ? null
  : "Enter a valid IPv4 address, for example 192.168.0.3.";

/** Both live tools (tshark capture, RTI listen) take 5 to 900 seconds. */
export const DURATION = { min: 5, max: 900 };
export const DURATION_PRESETS = [
  { s: 30, label: "30 sec" },
  { s: 60, label: "1 min" },
  { s: 300, label: "5 min" },
  { s: 900, label: "15 min" },
] as const;
export const TSHARK_DEFAULT_S = 60;
export const RTI_DEFAULT_S = 30;
export const RTI_DOMAINS = [7, 12, 13] as const;

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
}
export interface RtiChoice {
  domain: number;
  durationS: string;
}
export interface Choices {
  tshark: TsharkChoice;
  rti: RtiChoice;
}

export const defaultChoices = (): Choices => ({
  tshark: {
    mode: "run", durationS: String(TSHARK_DEFAULT_S), uploadId: null,
    filterKind: "quick", preset: "rti_all", port: "", proto: "any",
  },
  rti: { domain: RTI_DOMAINS[0], durationS: String(RTI_DEFAULT_S) },
});

const wholeSeconds = (v: string): string | null => {
  const n = Number(v);
  return v.trim() !== "" && Number.isInteger(n) && n >= DURATION.min && n <= DURATION.max
    ? null
    : `Enter a whole number of seconds from ${DURATION.min} to ${DURATION.max}.`;
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
      const e = wholeSeconds(c.tshark.durationS);
      if (e) errs["tshark.duration"] = e;
      if (c.tshark.filterKind === "manual" && !validPort(c.tshark.port))
        errs["tshark.port"] = "Enter a port number from 1 to 65535.";
    } else if (!c.tshark.uploadId) {
      errs["tshark.upload"] = "Choose a .pcap or .pcapng capture to import.";
    }
  }
  if (selected.has("rti")) {
    const e = wholeSeconds(c.rti.durationS);
    if (e) errs["rti.duration"] = e;
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
      out.tshark = { mode: "run", duration_s: Number(t.durationS), ...filter };
    }
  }
  if (selected.has("rti")) out.rti = { domain: c.rti.domain, duration_s: Number(c.rti.durationS) };
  return out;
}
