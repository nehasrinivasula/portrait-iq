// Pure helpers for the plugin picker: validation (D-9) and building the
// plugin_options payload. Limits mirror backend/app/collectors/*.py.

const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
export const validateTargetIp = (v: string): string | null =>
  !v.trim() ? "Enter the IP address of the Portrait Mobile server."
  : IPV4.test(v.trim()) ? null
  : "Enter a valid IPv4 address, for example 192.168.0.3.";

export const TSHARK_DURATION = { min: 5, max: 900, default: 60 };
export const RTI_DURATION = { min: 5, max: 900, default: 30 };
export const RTI_DOMAINS = [7, 12, 13] as const;

export interface TsharkChoice {
  mode: "run" | "import";
  durationS: string;
  uploadId: string | null;
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
  tshark: { mode: "run", durationS: String(TSHARK_DURATION.default), uploadId: null },
  rti: { domain: RTI_DOMAINS[0], durationS: String(RTI_DURATION.default) },
});

const wholeSeconds = (v: string, r: { min: number; max: number }): string | null => {
  const n = Number(v);
  return v.trim() !== "" && Number.isInteger(n) && n >= r.min && n <= r.max
    ? null
    : `Enter a whole number of seconds from ${r.min} to ${r.max}.`;
};

/** One message per field problem, keyed by field id, for the selected tools only. */
export function validateChoices(selected: Set<string>, c: Choices): Record<string, string> {
  const errs: Record<string, string> = {};
  if (selected.has("tshark")) {
    if (c.tshark.mode === "run") {
      const e = wholeSeconds(c.tshark.durationS, TSHARK_DURATION);
      if (e) errs["tshark.duration"] = e;
    } else if (!c.tshark.uploadId) {
      errs["tshark.upload"] = "Choose a .pcap or .pcapng capture to import.";
    }
  }
  if (selected.has("rti")) {
    const e = wholeSeconds(c.rti.durationS, RTI_DURATION);
    if (e) errs["rti.duration"] = e;
  }
  return errs;
}

/** Headers-only stays on (the backend default); the UI never offers full payload. */
export function buildOptions(selected: Set<string>, c: Choices): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  if (selected.has("tshark")) {
    out.tshark =
      c.tshark.mode === "run"
        ? { mode: "run", duration_s: Number(c.tshark.durationS) }
        : { mode: "import", upload_id: c.tshark.uploadId };
  }
  if (selected.has("rti")) out.rti = { domain: c.rti.domain, duration_s: Number(c.rti.durationS) };
  return out;
}
