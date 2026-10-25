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
    c.tshark.durationS = "60";
    expect(buildOptions(new Set(["tshark", "rti"]), c)).toEqual({
      tshark: { mode: "run", duration_s: 60 },
      rti: { domain: 7, duration_s: 30 },
    });
  });
});
