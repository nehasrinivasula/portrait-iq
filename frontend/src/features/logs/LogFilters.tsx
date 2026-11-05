import { useId } from "react";
import * as Label from "@radix-ui/react-label";
import * as ToggleGroup from "@radix-ui/react-toggle-group";
import TimeFramePicker from "./TimeFramePicker";
import type { TimeFrameChoice } from "./timeframe";
import type { ActiveFilter } from "./activeFilters";
import Dropdown, { type DropdownOption } from "../../ui/Dropdown";
import type { Facets, FacetValue, LogFilter, Severity } from "./logsApi";

const LEVELS: Severity[] = ["ERROR", "WARN", "INFO", "DEBUG"];

const toOptions = (values: FacetValue[] | undefined): DropdownOption[] =>
  (values ?? []).filter((v) => v.value).map((v) => ({ value: v.value!, count: v.count }));

interface Props {
  frame: TimeFrameChoice;
  onFrame: (f: TimeFrameChoice) => void;
  filter: LogFilter;
  onFilter: (f: LogFilter) => void;
  facets: Facets | null;
  active: ActiveFilter[];
  onRemove: (id: string) => void;
  onClear: () => void;
  masked: boolean;               // PHI/PII masking hides device and category values
}

// Everything that narrows the table lives here, above it, so the controls
// stay in view while the table scrolls.
export default function LogFilters({ frame, onFrame, filter, onFilter, facets, active, onRemove, onClear, masked }: Props) {
  const uid = useId();
  const set = (patch: Partial<LogFilter>) => onFilter({ ...filter, ...patch });
  const hidden = masked ? "Hidden while PHI / PII masking is on." : "None in these logs.";

  return (
    <section className="filters" aria-label="Filter logs">
      <div className="filter-row">
        <TimeFramePicker value={frame} onChange={onFrame} allowAny limitDays={null} legend="Date" compact />
        <div className="filter-group">
          <span className="filter-label" id={`${uid}-src`} title="Which log the entry came from, e.g. the Portrait device log or Kubernetes.">Source</span>
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
        <Dropdown id={`${uid}-dev`} label="Device" value={filter.device} onChange={(v) => set({ device: v })}
          options={toOptions(facets?.devices)} anyLabel="Any device" emptyText={hidden}
          description="The machine that wrote the entry: a server, hub or viewer, by its host name." />
        <Dropdown id={`${uid}-cat`} label="Category" value={filter.category} onChange={(v) => set({ category: v })}
          options={toOptions(facets?.categories)} anyLabel="Any category" emptyText={hidden}
          description="The kind of event, as the system labels it (e.g. General, PerfData). Source is where the log came from; category is what the entry is about." />
        <div className="field">
          <Label.Root htmlFor={`${uid}-proc`}>Process</Label.Root>
          <input id={`${uid}-proc`} value={filter.process} autoComplete="off" placeholder="e.g. axonerouter"
            onChange={(e) => set({ process: e.target.value })} />
        </div>
        <div className="field grow">
          <Label.Root htmlFor={`${uid}-q`}>Search messages</Label.Root>
          <input id={`${uid}-q`} type="search" value={filter.q} autoComplete="off" placeholder="Words in the log message"
            onChange={(e) => set({ q: e.target.value })} />
        </div>
        <button type="button" className="btn ghost" onClick={onClear} disabled={active.length === 0}>
          Clear filters{active.length ? ` (${active.length})` : ""}
        </button>
      </div>

      <div className="active-filters" aria-live="polite">
        <span className="filter-label">Active filters</span>
        {active.length === 0 ? (
          <span className="muted">None, showing all logs.</span>
        ) : (
          <>
            <ul className="chips" aria-label="Active filters">
              {active.map((a) => (
                <li key={a.id}>
                  <button type="button" className="chip remove-chip" onClick={() => onRemove(a.id)}
                    aria-label={`Remove filter ${a.label}: ${a.value}`}>
                    {a.label}: {a.value} <span aria-hidden="true">×</span>
                  </button>
                </li>
              ))}
            </ul>
            <span className="muted">Logs must match every filter.</span>
          </>
        )}
      </div>
    </section>
  );
}
