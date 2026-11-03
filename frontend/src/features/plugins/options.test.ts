import { describe, expect, it } from "vitest";
import { buildOptions, defaultChoices, validateChoices, validateTargetIp } from "./options";

describe("validateTargetIp", () => {
  it("accepts IPv4 and rejects the rest", () => {
    expect(validateTargetIp("192.168.0.3")).toBeNull();
    expect(validateTargetIp("")).toMatch(/Enter the IP/);
    expect(validateTargetIp("192.168.0.300")).toMatch(/valid IPv4/);
    expect(validateTargetIp("host.local")).toMatch(/valid IPv4/);
  });
});

describe("validateChoices / buildOptions", () => {
  it("only validates and sends options for selected tools", () => {
    const c = defaultChoices();
    c.rti.durationS = "1";
    expect(validateChoices(new Set(["netshoot"]), c)).toEqual({});
    expect(buildOptions(new Set(["netshoot"]), c)).toEqual({});
  });

  it("requires an uploaded capture for tshark import", () => {
    const c = defaultChoices();
    c.tshark.mode = "import";
    expect(validateChoices(new Set(["tshark"]), c)["tshark.upload"]).toBeDefined();
    c.tshark.uploadId = "abc";
    expect(validateChoices(new Set(["tshark"]), c)).toEqual({});
    expect(buildOptions(new Set(["tshark"]), c)).toEqual({ tshark: { mode: "import", upload_id: "abc" } });
  });

  it("bounds capture durations", () => {
    const c = defaultChoices();
    c.tshark.durationS = "901";
    expect(validateChoices(new Set(["tshark"]), c)["tshark.duration"]).toBeDefined();
    c.tshark.durationS = "900";   // tshark takes up to 15 minutes
    expect(validateChoices(new Set(["tshark"]), c)["tshark.duration"]).toBeUndefined();
    for (const bad of ["9", "301", "900"]) {   // RTI only 10 to 300 s (rti.py)
      c.rti.durationS = bad;
      expect(validateChoices(new Set(["rti"]), c)["rti.duration"]).toBeDefined();
    }
    c.rti.durationS = "30";
    c.tshark.durationS = "60";
    expect(buildOptions(new Set(["tshark", "rti"]), c)).toEqual({
      tshark: { mode: "run", duration_s: 60, filter_preset: "rti_all" },
      rti: { domain: 7, duration_s: 30 },
    });
  });

  it("sends exactly one live filter: a quick preset or a typed port", () => {
    const c = defaultChoices();
    c.tshark.preset = "rti_view_pod";
    expect(buildOptions(new Set(["tshark"]), c).tshark).toEqual({ mode: "run", duration_s: 60, filter_preset: "rti_view_pod" });

    c.tshark.filterKind = "manual";
    expect(validateChoices(new Set(["tshark"]), c)["tshark.port"]).toBeDefined();
    for (const bad of ["0", "65536", "80.5", "abc"]) {
      c.tshark.port = bad;
      expect(validateChoices(new Set(["tshark"]), c)["tshark.port"]).toBeDefined();
    }
    c.tshark.port = "7400";
    expect(validateChoices(new Set(["tshark"]), c)).toEqual({});
    expect(buildOptions(new Set(["tshark"]), c).tshark).toEqual({ mode: "run", duration_s: 60, capture_filter: "port 7400" });
    c.tshark.proto = "udp";
    expect(buildOptions(new Set(["tshark"]), c).tshark).toMatchObject({ capture_filter: "udp port 7400" });
  });
});
