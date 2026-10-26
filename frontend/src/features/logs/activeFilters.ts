import type { LogFilter } from "./logsApi";
import { PRESETS, type TimeFrameChoice } from "./timeframe";

// One removable chip per applied filter (SCRUM-54), so the engineer sees what
// narrows the table and can drop one condition without clearing the rest.

export interface ActiveFilter {
  id: string;
  label: string;
  value: string;
}

const COLUMN_LABELS = { device: "Device", category: "Category", process: "Process" } as const;
const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

function dateText(frame: TimeFrameChoice): string {
  if (frame.preset === "custom") {
    const show = (v: string) => (v && !isNaN(new Date(v).getTime()) ? fmt.format(new Date(v)) : "…");
    return `${show(frame.customSince)} – ${show(frame.customUntil)}`;
  }
  return PRESETS.find((p) => p.id === frame.preset)?.label ?? frame.preset;
}

export function activeFilters(frame: TimeFrameChoice, filter: LogFilter): ActiveFilter[] {
  const out: ActiveFilter[] = [];
  if (frame.preset !== "any") out.push({ id: "date", label: "Date", value: dateText(frame) });
  filter.sources.forEach((s) => out.push({ id: `source:${s}`, label: "Source", value: s }));
  filter.severities.forEach((s) => out.push({ id: `severity:${s}`, label: "Level", value: s }));
  if (filter.value.trim()) out.push({ id: "value", label: COLUMN_LABELS[filter.column], value: filter.value.trim() });
  if (filter.q.trim()) out.push({ id: "q", label: "Message has", value: filter.q.trim() });
  return out;
}

/** The frame and filter with one chip's condition taken out. */
export function removeFilter(id: string, frame: TimeFrameChoice, filter: LogFilter): { frame: TimeFrameChoice; filter: LogFilter } {
  if (id === "date") return { frame: { ...frame, preset: "any" }, filter };
  const [kind, value] = id.split(/:(.*)/s);
  if (kind === "source") return { frame, filter: { ...filter, sources: filter.sources.filter((s) => s !== value) } };
  if (kind === "severity") return { frame, filter: { ...filter, severities: filter.severities.filter((s) => s !== value) } };
  if (kind === "value") return { frame, filter: { ...filter, value: "" } };
  if (kind === "q") return { frame, filter: { ...filter, q: "" } };
  return { frame, filter };
}
