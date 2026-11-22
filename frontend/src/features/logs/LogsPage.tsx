import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import ImportLogs from "./ImportLogs";
import LogFilters from "./LogFilters";
import LogTable from "./LogTable";
import LogSets, { fileName } from "./LogSets";
import { activeFilters, removeFilter } from "./activeFilters";
import { emptyFilter, getFacets, IMPORTED, type Facets, type LogFilter } from "./logsApi";
import { defaultTimeFrame, filterRange, type TimeFrameChoice } from "./timeframe";
import PrivacyPanel from "../privacy/PrivacyPanel";
import { noPrivacyOptions, type PrivacyOptions } from "../privacy/privacyApi";

const DEBOUNCE_MS = 300;

// Keyed on the value's JSON: callers pass a new object each render, and
// restarting the timer on identity alone re-ran the search every 300 ms,
// so a search slower than that never finished ("Loading logs…" forever).
function useDebounced<T>(value: T, ms: number): T {
  const key = JSON.stringify(value);
  const [v, setV] = useState(value);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { const t = window.setTimeout(() => setV(value), ms); return () => window.clearTimeout(t); }, [key, ms]);
  return v;
}

// Log sets on the left (all logs, imports, each run, and their files); on the
// right the filters (always in view) and the log table, scrolling on its own.
export default function LogsPage() {
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const file = params.get("file");
  const [frame, setFrame] = useState<TimeFrameChoice>({ ...defaultTimeFrame(), preset: "any" });
  const [filter, setFilter] = useState<LogFilter>(emptyFilter);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [total, setTotal] = useState<number | null>(null);
  const [privacy, setPrivacy] = useState<PrivacyOptions>(noPrivacyOptions);

  // A custom range that is half-typed or invalid keeps the last good one applied.
  const lastRange = useRef<{ since: string | null; until: string | null }>({ since: null, until: null });
  const range = useMemo(() => {
    const r = filterRange(frame);
    if (r.ok) lastRange.current = { since: r.since, until: r.until };
    return lastRange.current;
  }, [frame]);

  const applied = useDebounced<LogFilter>({ ...filter, ...range, jobId, sourceFile: file ?? "" }, DEBOUNCE_MS);

  // Options come from the date range and run only, so picking one doesn't hide the others.
  const optionScope = useDebounced({ ...emptyFilter(), ...range, jobId, sourceFile: file ?? "" }, DEBOUNCE_MS);
  useEffect(() => {
    let live = true;
    getFacets(optionScope, privacy.mask).then((f) => live && setFacets(f)).catch(() => live && setFacets(null));
    return () => { live = false; };
  }, [optionScope, reloadKey, privacy.mask]);

  const pick = (set: string | null, f: string | null) => {
    const next: Record<string, string> = {};
    if (set) next.job = set;
    if (f) next.file = f;
    setParams(next);
  };
  const active = activeFilters(frame, { ...filter, sourceFile: file ?? "" });
  const onRemove = (id: string) => {
    if (id === "file") { pick(jobId, null); return; }
    const next = removeFilter(id, frame, filter);
    setFrame(next.frame);
    setFilter(next.filter);
  };
  const onClear = () => {
    setFilter(emptyFilter());
    setFrame({ ...defaultTimeFrame(), preset: "any" });
    if (file) pick(jobId, null);
  };
  const onTotal = useCallback((n: number | null) => setTotal(n), []);

  const scope = jobId ?? "all";
  const title = jobId === null ? "All logs" : jobId === IMPORTED ? "Imported files" : `Run ${jobId.slice(0, 8)}`;
  return (
    <div className="logs-layout">
      <LogSets selected={jobId} file={file} onSelect={pick} reloadKey={reloadKey} />
      <div className="logs-page">
        <div className="logs-head">
          <div className="logs-title">
            <h1>Logs</h1>
            <p className="logs-head-sub">
              {title}{file ? ` › ${fileName(file)}` : ""}{total !== null && ` · ${total.toLocaleString()} match${total === 1 ? "" : "es"}`}
            </p>
          </div>
          <div className="logs-head-actions">
            <ImportLogs onImported={() => setReloadKey((k) => k + 1)} />
            <Link to={`/export/${encodeURIComponent(scope)}?from=logs`} className="btn">
              {jobId === null ? "Export all logs" : jobId === IMPORTED ? "Export imported files" : "Export this run"}
            </Link>
          </div>
        </div>

        <PrivacyPanel jobId={jobId} options={privacy} onOptions={setPrivacy} reloadKey={reloadKey}
          onScanned={() => setReloadKey((k) => k + 1)} />

        <LogFilters frame={frame} onFrame={setFrame} filter={filter} onFilter={setFilter} masked={privacy.mask}
          facets={privacy.mask && facets ? { ...facets, devices: [], categories: [] } : facets} active={active} onRemove={onRemove} onClear={onClear} />

        <div className="logs-body">
          <LogTable filter={applied} reloadKey={reloadKey} onTotal={onTotal} privacy={privacy}
            filtered={active.length > 0 || !!jobId || privacy.flaggedOnly} />
        </div>
      </div>
    </div>
  );
}
