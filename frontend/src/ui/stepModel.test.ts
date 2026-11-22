import { describe, expect, it } from "vitest";
import { formatDuration, newlyFinished, pollDelay, stepPosition, totalDuration, type StepItem, type StepStatus } from "./stepModel";

const step = (id: string, status: StepStatus, startedAt?: number, finishedAt?: number): StepItem =>
  ({ id, label: id, status, startedAt, finishedAt });

describe("stepPosition", () => {
  it("counts the running step", () => {
    expect(stepPosition([step("a", "done"), step("b", "running"), step("c", "pending")])).toEqual({ position: 2, total: 3 });
  });
  it("has no position before the start or after the end", () => {
    expect(stepPosition([step("a", "pending")]).position).toBeNull();
    expect(stepPosition([step("a", "done"), step("b", "warn")]).position).toBeNull();
  });
});

describe("formatDuration", () => {
  it("keeps a decimal for fast steps and switches to minutes past one", () => {
    expect(formatDuration(420)).toBe("0.4 s");
    expect(formatDuration(12_400)).toBe("12 s");
    expect(formatDuration(65_000)).toBe("1 min 05 s");
    expect(formatDuration(-5)).toBe("0.0 s");
  });
});

describe("totalDuration", () => {
  it("runs from the first start to the last finish", () => {
    expect(totalDuration([step("a", "done", 1000, 1500), step("b", "done", 1500, 5100)])).toBe("4.1 s");
    expect(totalDuration([step("a", "pending")])).toBeNull();
  });
});

describe("newlyFinished", () => {
  it("lists steps that finished since the last render, in order, and none on first paint", () => {
    const now = [step("a", "done"), step("b", "warn"), step("c", "running")];
    expect(newlyFinished(null, now)).toEqual([]);
    expect(newlyFinished(new Map([["a", "running"], ["b", "pending"], ["c", "pending"]]), now)).toEqual(["a", "b"]);
    expect(newlyFinished(new Map([["a", "done"], ["b", "warn"]]), now)).toEqual([]);
  });
});

describe("pollDelay", () => {
  it("asks often at first, then backs off", () => {
    expect(pollDelay(0)).toBe(400);
    expect(pollDelay(30_000)).toBe(1500);
  });
});
