import { describe, expect, it } from "vitest";
import { percent, segmentHeights } from "./metrics";

describe("metrics helpers", () => {
  it("rounds percentages and never shows a real count as 0%", () => {
    expect(percent(1, 1000)).toBe(1);
    expect(percent(0, 1000)).toBe(0);
    expect(percent(1, 3)).toBe(33);
    expect(percent(5, 0)).toBe(0);
  });
  it("scales segments to the busiest bar", () => {
    const h = segmentHeights({ ERROR: 5, WARN: 5 }, 20);
    expect(h.ERROR).toBe(25);
    expect(h.WARN).toBe(25);
    expect(h.INFO).toBe(0);
    expect(segmentHeights({ ERROR: 1 }, 0).ERROR).toBe(0);
  });
});
