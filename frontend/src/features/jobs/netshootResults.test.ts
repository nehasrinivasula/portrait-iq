import { describe, expect, it } from "vitest";
import { netshootSummary } from "./netshootResults";

describe("Netshoot check summary", () => {
  it("counts individual pass/fail outcomes", () => {
    expect(netshootSummary([
      { label: "DNS", status: "pass", reason: null },
      { label: "TCP 443", status: "fail", reason: "Connection refused." },
    ])).toBe("1 of 2 network checks passed");
  });

  it("handles an empty result without inventing a pass", () => {
    expect(netshootSummary([])).toBe("0 of 0 network checks passed");
  });
});
