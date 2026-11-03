import { describe, expect, it } from "vitest";
import { checkSummary } from "./checks";

describe("checkSummary", () => {
  it("counts passes out of all checks", () => {
    expect(checkSummary([
      { label: "ping", status: "pass", reason: null },
      { label: "TCP 22", status: "fail", reason: "Connection refused." },
    ])).toBe("1 of 2 checks passed");
  });
  it("does not invent a pass for an empty list", () => {
    expect(checkSummary([])).toBe("0 of 0 checks passed");
  });
});
