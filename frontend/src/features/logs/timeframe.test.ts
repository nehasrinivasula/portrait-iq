import { describe, expect, it } from "vitest";
import { defaultTimeFrame, resolveTimeFrame, toApi, toLocalInput } from "./timeframe";

const now = new Date("2026-10-25T20:00:00Z");

describe("resolveTimeFrame", () => {
  it("resolves presets back from now", () => {
    const r = resolveTimeFrame({ ...defaultTimeFrame(), preset: "6h" }, now);
    expect(r.ok && toApi(r)).toEqual({ since: "2026-10-25T14:00:00.000Z", until: "2026-10-25T20:00:00.000Z" });
  });

  it("validates a custom window", () => {
    const c = { preset: "custom" as const, customSince: "", customUntil: "" };
    expect(resolveTimeFrame(c, now)).toMatchObject({ ok: false, errors: { since: expect.any(String), until: expect.any(String) } });

    const old = toLocalInput(new Date(now.getTime() - 31 * 864e5));
    expect(resolveTimeFrame({ ...c, customSince: old, customUntil: toLocalInput(now) }, now))
      .toMatchObject({ ok: false, errors: { since: expect.stringMatching(/30 days/) } });

    const a = toLocalInput(new Date(now.getTime() - 3600e3));
    expect(resolveTimeFrame({ ...c, customSince: a, customUntil: a }, now))
      .toMatchObject({ ok: false, errors: { until: expect.stringMatching(/after the start/) } });

    const future = toLocalInput(new Date(now.getTime() + 3600e3));
    expect(resolveTimeFrame({ ...c, customSince: a, customUntil: future }, now))
      .toMatchObject({ ok: false, errors: { until: expect.stringMatching(/future/) } });
  });

  it("accepts a valid custom window", () => {
    const since = toLocalInput(new Date(now.getTime() - 2 * 3600e3));
    const until = toLocalInput(new Date(now.getTime() - 3600e3));
    const r = resolveTimeFrame({ preset: "custom", customSince: since, customUntil: until }, now);
    expect(r.ok).toBe(true);
  });
});
