import { useId } from "react";
import * as Label from "@radix-ui/react-label";
import * as ToggleGroup from "@radix-ui/react-toggle-group";
import TimeFramePicker from "./TimeFramePicker";
import type { TimeFrameChoice } from "./timeframe";
import type { Facets, FieldColumn, LogFilter, Severity } from "./logsApi";

const LEVELS: Severity[] = ["ERROR", "WARN", "INFO", "DEBUG"];
const COLUMNS: { id: FieldColumn; label: string }[] = [
  { id: "device", label: "Device" },
  { id: "category", label: "Category" },
  { id: "process", label: "Process" },
];

interface Props {
  frame: TimeFrameChoice;
  onFrame: (f: TimeFrameChoice) => void;
  filter: LogFilter;
  onFilter: (f: LogFilter) => void;
  facets: Facets | null;
  activeCount: number;
  onClear: () => void;
}

// Everything that narrows the table lives here, above it, so the controls
// stay in view while the table scrolls.
export default function LogFilters({ frame, onFrame, filter, onFilter, facets, activeCount, onClear }: Props) {
  const uid = useId();
  const set = (patch: Partial<LogFilter>) => onFilter({ ...filter, ...patch });
  const options =
    filter.column === "device" ? facets?.devices : filter.column === "category" ? facets?.categories : undefined;

  return (
    <section className="filters" aria-label="Filter logs">
      <div className="filter-row">
        <TimeFramePicker value={frame} onChange={onFrame} allowAny limitDays={null} legend="Date" compact />
        <div className="filter-group">
          <span className="filter-label" id={`${uid}-src`}>Source</span>
          <ToggleGroup.Root type="multiple" className="chips" aria-labelledby={`${uid}-src`}
            value={filter.sources} onValueChange={(v) => set({ sources: v })}>
            {facets?.sources.filter((s) => s.value).map((s) => (
              <ToggleGroup.Item key={s.value} value={s.value!} className="chip">{s.value} <span className="muted">{s.count}</span></ToggleGroup.Item>
            ))}
            {facets && facets.sources.length === 0 && <span className="muted">No sources yet</span>}
          </ToggleGroup.Root>
        </div>

        <div className="filter-group">
          <span className="filter-label" id={`${uid}-lvl`}>Level</span>
          <ToggleGroup.Root type="multiple" className="chips" aria-labelledby={`${uid}-lvl`}
            value={filter.severities} onValueChange={(v) => set({ severities: v as Severity[] })}>
            {LEVELS.map((l) => (
              <ToggleGroup.Item key={l} value={l} className={`chip lvl-${l}`}>{l}</ToggleGroup.Item>
            ))}
          </ToggleGroup.Root>
        </div>
      </div>

      <div className="filter-row">
        <div className="field">
          <Label.Root htmlFor={`${uid}-col`}>Column</Label.Root>
          <select id={`${uid}-col`} value={filter.column} onChange={(e) => set({ column: e.target.value as FieldColumn, value: "" })}>
            {COLUMNS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select>
        </div>
        <div className="field">
          <Label.Root htmlFor={`${uid}-val`}>Equals</Label.Root>
          <input id={`${uid}-val`} list={options ? `${uid}-opts` : undefined} value={filter.value} autoComplete="off"
            placeholder={options ? "Pick or type a value" : "Type a value"} onChange={(e) => set({ value: e.target.value })} />
          {options && <datalist id={`${uid}-opts`}>{options.filter((o) => o.value).map((o) => <option key={o.value} value={o.value!} />)}</datalist>}
        </div>
        <div className="field grow">
          <Label.Root htmlFor={`${uid}-q`}>Search messages</Label.Root>
          <input id={`${uid}-q`} type="search" value={filter.q} autoComplete="off" placeholder="Words in the log message"
            onChange={(e) => set({ q: e.target.value })} />
        </div>
        <button type="button" className="btn ghost" onClick={onClear} disabled={activeCount === 0}>
          Clear filters{activeCount ? ` (${activeCount})` : ""}
        </button>
      </div>
    </section>
  );
}
