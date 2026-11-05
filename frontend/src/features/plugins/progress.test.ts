import { describe, expect, it } from "vitest";
import { formatElapsed, parseServerTime, STEPS, stepLabel, stepProgress } from "./progress";

describe("stepProgress", () => {
  it("marks earlier steps done, the current one current, the rest upcoming", () => {
    const p = stepProgress("collect_server_logs");
    expect(p.position).toBe(2);
    expect(p.total).toBe(6);
    expect(p.steps.map((s) => s.state)).toEqual(["done", "current", "upcoming", "upcoming", "upcoming", "upcoming"]);
    expect(p.steps[1].label).toBe("Collecting logs");
  });

  it("handles the last step", () => {
    const p = stepProgress("package");
    expect(p.position).toBe(STEPS.length);
    expect(p.steps[p.steps.length - 1].state).toBe("current");
    expect(p.steps.slice(0, -1).every((s) => s.state === "done")).toBe(true);
  });

  it("has no position before the first step or for an unknown step", () => {
    for (const step of [null, "something_new"]) {
      const p = stepProgress(step);
      expect(p.position).toBeNull();
      expect(p.steps.every((s) => s.state === "upcoming")).toBe(true);
    }
  });

  it("labels every step in plain words and falls back to the raw name", () => {
    for (const s of STEPS) expect(stepLabel(s)).not.toBe(s);
    expect(stepLabel("something_new")).toBe("something_new");
  });
});

describe("parseServerTime", () => {
  it("reads a time without an offset as UTC", () => {
    expect(parseServerTime("2026-11-05T10:00:00")).toBe(Date.UTC(2026, 10, 5, 10, 0, 0));
    expect(parseServerTime("2026-11-05T10:00:00.250000")).toBe(Date.UTC(2026, 10, 5, 10, 0, 0, 250));
  });

  it("keeps an explicit offset", () => {
    expect(parseServerTime("2026-11-05T10:00:00Z")).toBe(Date.UTC(2026, 10, 5, 10, 0, 0));
    expect(parseServerTime("2026-11-05T12:00:00+02:00")).toBe(Date.UTC(2026, 10, 5, 10, 0, 0));
  });

  it("returns null for missing or unreadable times", () => {
    expect(parseServerTime(null)).toBeNull();
    expect(parseServerTime("")).toBeNull();
    expect(parseServerTime("not a time")).toBeNull();
  });
});

describe("formatElapsed", () => {
  it("formats seconds, minutes and hours", () => {
    expect(formatElapsed(0)).toBe("0 s");
    expect(formatElapsed(45_900)).toBe("45 s");
    expect(formatElapsed(185_000)).toBe("3 min 05 s");
    expect(formatElapsed(3_720_000)).toBe("1 h 02 min");
  });

  it("never shows a negative time", () => {
    expect(formatElapsed(-5000)).toBe("0 s");
  });
});
