import type { LogFilter } from "./logsApi";
import { PRESETS, type TimeFrameChoice } from "./timeframe";

// One removable chip per applied filter (SCRUM-54), so the engineer sees what
// narrows the table and can drop one condition without clearing the rest.

export interface ActiveFilter {
  id: string;
  label: string;
  value: string;
}

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
  if (filter.device.trim()) out.push({ id: "device", label: "Device", value: filter.device.trim() });
  if (filter.category.trim()) out.push({ id: "category", label: "Category", value: filter.category.trim() });
  if (filter.process.trim()) out.push({ id: "process", label: "Process", value: filter.process.trim() });
  // Stored names carry where the file was staged (local/<hash>/name.csv): show the name.
  if (filter.sourceFile) out.push({ id: "file", label: "File", value: filter.sourceFile.split("/").pop() || filter.sourceFile });
  if (filter.q.trim()) out.push({ id: "q", label: "Message has", value: filter.q.trim() });
  return out;
}

/** The frame and filter with one chip's condition taken out. */
export function removeFilter(id: string, frame: TimeFrameChoice, filter: LogFilter): { frame: TimeFrameChoice; filter: LogFilter } {
  if (id === "date") return { frame: { ...frame, preset: "any" }, filter };
  const [kind, value] = id.split(/:(.*)/s);
  if (kind === "source") return { frame, filter: { ...filter, sources: filter.sources.filter((s) => s !== value) } };
  if (kind === "severity") return { frame, filter: { ...filter, severities: filter.severities.filter((s) => s !== value) } };
  if (kind === "device") return { frame, filter: { ...filter, device: "" } };
  if (kind === "category") return { frame, filter: { ...filter, category: "" } };
  if (kind === "process") return { frame, filter: { ...filter, process: "" } };
  if (kind === "file") return { frame, filter: { ...filter, sourceFile: "" } };
  if (kind === "q") return { frame, filter: { ...filter, q: "" } };
  return { frame, filter };
}
