// Small helpers for the metrics panel: plain arithmetic on the counts the server returns.

export const LEVELS = ["ERROR", "WARN", "INFO", "DEBUG"] as const;

/** Whole-number percentage; a non-zero part never shows as 0%. */
export function percent(part: number, whole: number): number {
  return whole ? Math.max(part ? 1 : 0, Math.round((part / whole) * 100)) : 0;
}

/** Heights (0..100) of the stacked segments in one bar, scaled to the busiest bar. */
export function segmentHeights(counts: Record<string, number>, busiest: number): Record<string, number> {
  const out: Record<string, number> = {};
  for (const l of LEVELS) out[l] = busiest ? ((counts[l] ?? 0) / busiest) * 100 : 0;
  return out;
}

/** Axis label for a bucket start, in the viewer's time zone. */
export function bucketLabel(start: string, bucket: "minute" | "hour" | "day"): string {
  const d = new Date(`${start}Z`);
  return bucket === "day"
    ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: bucket === "minute" ? "2-digit" : undefined });
}
