// Time-frame selection for log pulls (SCRUM-45). The backend caps a window at
// 30 days and reads times without an offset as UTC, so we always send UTC ISO.

export const MAX_DAYS = 30;
const H = 3_600_000;

export const PRESETS = [
  { id: "1h", label: "Last hour", ms: H },
  { id: "6h", label: "Last 6 hours", ms: 6 * H },
  { id: "24h", label: "Last 24 hours", ms: 24 * H },
  { id: "7d", label: "Last 7 days", ms: 7 * 24 * H },
] as const;

export type PresetId = (typeof PRESETS)[number]["id"] | "custom" | "any";

export interface TimeFrameChoice {
  preset: PresetId;
  /** <input type="datetime-local"> values, local time. Used when preset is "custom". */
  customSince: string;
  customUntil: string;
}

export const defaultTimeFrame = (): TimeFrameChoice => ({ preset: "24h", customSince: "", customUntil: "" });

export type Resolved =
  | { ok: true; since: Date; until: Date }
  | { ok: false; errors: { since?: string; until?: string } };

/** limitDays: how far back a window may start. Pulling logs is capped by the
 * backend (30 days); filtering logs already stored has no cap (null). */
export function resolveTimeFrame(c: TimeFrameChoice, now: Date = new Date(), limitDays: number | null = MAX_DAYS): Resolved {
  if (c.preset === "any") return { ok: true, since: new Date(0), until: now };
  if (c.preset !== "custom") {
    const ms = PRESETS.find((p) => p.id === c.preset)!.ms;
    return { ok: true, since: new Date(now.getTime() - ms), until: now };
  }
  const errors: { since?: string; until?: string } = {};
  const since = c.customSince ? new Date(c.customSince) : null;
  const until = c.customUntil ? new Date(c.customUntil) : null;
  const earliest = limitDays === null ? new Date(0) : new Date(now.getTime() - limitDays * 24 * H);

  if (!since || isNaN(since.getTime())) errors.since = "Choose a start date and time.";
  else if (since < earliest) errors.since = `The start can't be more than ${limitDays} days ago.`;
  else if (since > now) errors.since = "The start can't be in the future.";

  if (!until || isNaN(until.getTime())) errors.until = "Choose an end date and time.";
  else if (until > now) errors.until = "The end can't be in the future.";

  if (!errors.since && !errors.until && since!.getTime() >= until!.getTime())
    errors.until = "The end must be after the start.";

  return errors.since || errors.until ? { ok: false, errors } : { ok: true, since: since!, until: until! };
}

/** Query/body values for the API: ISO 8601 in UTC. */
export const toApi = (r: Extract<Resolved, { ok: true }>) => ({
  since: r.since.toISOString(),
  until: r.until.toISOString(),
});

/** datetime-local value (local time, minute precision) for min/max attributes and defaults. */
export function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** For filtering stored logs: no bounds at all when the choice is "any". */
export function filterRange(c: TimeFrameChoice, now: Date = new Date()):
  | { ok: true; since: string | null; until: string | null }
  | { ok: false; errors: { since?: string; until?: string } } {
  if (c.preset === "any") return { ok: true, since: null, until: null };
  const r = resolveTimeFrame(c, now, null);
  return r.ok ? { ok: true, ...toApi(r) } : r;
}
