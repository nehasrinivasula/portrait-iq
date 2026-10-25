import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ImportLogs from "./ImportLogs";
import LogFilters from "./LogFilters";
import LogTable from "./LogTable";
import { emptyFilter, getFacets, type Facets, type LogFilter } from "./logsApi";
import { defaultTimeFrame, filterRange, type TimeFrameChoice } from "./timeframe";

const DEBOUNCE_MS = 300;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = window.setTimeout(() => setV(value), ms); return () => window.clearTimeout(t); }, [value, ms]);
  return v;
}

// One page: filters on top (always in view), the log table below and scrolling
// on its own. Filtering by date, source, level, column value and text.
export default function LogsPage() {
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const [frame, setFrame] = useState<TimeFrameChoice>({ ...defaultTimeFrame(), preset: "any" });
  const [filter, setFilter] = useState<LogFilter>(emptyFilter);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [total, setTotal] = useState<number | null>(null);

  // A custom range that is half-typed or invalid keeps the last good one applied.
  const lastRange = useRef<{ since: string | null; until: string | null }>({ since: null, until: null });
  const range = useMemo(() => {
    const r = filterRange(frame);
    if (r.ok) lastRange.current = { since: r.since, until: r.until };
    return lastRange.current;
  }, [frame]);

  const applied = useDebounced<LogFilter>({ ...filter, ...range, jobId }, DEBOUNCE_MS);

  // Options come from the date range and run only, so picking one doesn't hide the others.
  const optionScope = useDebounced({ ...emptyFilter(), ...range, jobId }, DEBOUNCE_MS);
  useEffect(() => {
    let live = true;
    getFacets(optionScope).then((f) => live && setFacets(f)).catch(() => live && setFacets(null));
    return () => { live = false; };
  }, [optionScope, reloadKey]);

  const activeCount =
    (frame.preset !== "any" ? 1 : 0) + filter.sources.length + filter.severities.length +
    (filter.value.trim() ? 1 : 0) + (filter.q.trim() ? 1 : 0);
  const onClear = () => { setFilter(emptyFilter()); setFrame({ ...defaultTimeFrame(), preset: "any" }); };
  const onTotal = useCallback((n: number | null) => setTotal(n), []);

  return (
    <div className="logs-page">
      <div className="logs-head">
        <h1>Logs{total !== null && <span className="muted" style={{ fontWeight: 400, fontSize: 18 }}> · {total} match{total === 1 ? "" : "es"}</span>}</h1>
        <ImportLogs onImported={() => setReloadKey((k) => k + 1)} />
      </div>

      {jobId && (
        <p className="job-chip" role="status">
          Showing logs from run <code>{jobId.slice(0, 8)}</code>{" "}
          <button className="btn ghost" onClick={() => setParams({})}>Show all logs</button>
        </p>
      )}

      <LogFilters frame={frame} onFrame={setFrame} filter={filter} onFilter={setFilter}
        facets={facets} activeCount={activeCount} onClear={onClear} />

      <div className="logs-body">
        <LogTable filter={applied} reloadKey={reloadKey} onTotal={onTotal} filtered={activeCount > 0 || !!jobId} />
      </div>
    </div>
  );
}
