import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import ImportLogs from "./ImportLogs";
import LogFilters from "./LogFilters";
import LogTable from "./LogTable";
import SourcePicker, { fileName } from "./SourcePicker";
import InsightsPanel from "./InsightsPanel";
import MetricsPanel from "./MetricsPanel";
import { activeFilters, removeFilter } from "./activeFilters";
import { emptyFilter, getFacets, getMetrics, IMPORTED, type Facets, type LogFilter, type Metrics, type MetricsGroup } from "./logsApi";
import { defaultTimeFrame, filterRange, type TimeFrameChoice } from "./timeframe";
import PrivacyPanel from "../privacy/PrivacyPanel";
import { noPrivacyOptions, type PrivacyOptions } from "../privacy/privacyApi";

const TYPING_MS = 300;

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

/** The same object until its JSON changes, so effects keyed on it run once per real change. */
function useStable<T>(value: T): T {
  const key = JSON.stringify(value);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => value, [key]);
}

// One toolbar (which logs, then how to narrow them), two one-line summaries
// (insights, PHI / PII) that open for detail, then the table, scrolling on its own.
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

  // Only typed text waits for a pause; clicks (chips, dropdowns, dates) apply at once.
  const q = useDebounced(filter.q, TYPING_MS);
  const process = useDebounced(filter.process, TYPING_MS);
  const applied = useStable<LogFilter>({ ...filter, q, process, ...range, jobId, sourceFile: file ?? "" });

  // Options come from the date range and run only, so picking one doesn't hide the others.
  const optionScope = useStable({ ...emptyFilter(), ...range, jobId, sourceFile: file ?? "" });
  useEffect(() => {
    const ac = new AbortController();   // a newer scope cancels the older request instead of racing it
    getFacets(optionScope, privacy.mask, ac.signal)
      .then((f) => { if (!ac.signal.aborted) setFacets(f); })
      .catch(() => { if (!ac.signal.aborted) setFacets(null); });
    return () => ac.abort();
  }, [optionScope, reloadKey, privacy.mask]);

  // Insights follow every filter, unlike the dropdown options above, so hiding noise shows its effect.
  const [insights, setInsights] = useState<Facets | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    getFacets(applied, privacy.mask, ac.signal)
      .then((f) => { if (!ac.signal.aborted) setInsights(f); })
      .catch(() => { if (!ac.signal.aborted) setInsights(null); });
    return () => ac.abort();
  }, [applied, reloadKey, privacy.mask]);

  // Counts per level, over time and by service; follow every filter, like the insights.
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [groupBy, setGroupBy] = useState<MetricsGroup>("process");
  useEffect(() => {
    const ac = new AbortController();
    getMetrics(applied, groupBy, privacy.mask, ac.signal)
      .then((m) => { if (!ac.signal.aborted) setMetrics(m); })
      .catch(() => { if (!ac.signal.aborted) setMetrics(null); });
    return () => ac.abort();
  }, [applied, groupBy, reloadKey, privacy.mask]);

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

      <div className="toolbar">
        <SourcePicker selected={jobId} file={file} onSelect={pick} reloadKey={reloadKey} />
        <LogFilters frame={frame} onFrame={setFrame} filter={filter} onFilter={setFilter} masked={privacy.mask}
          facets={privacy.mask && facets ? { ...facets, devices: [], categories: [] } : facets} active={active} onRemove={onRemove} onClear={onClear} />
      </div>

      <InsightsPanel facets={insights} filter={filter} onFilter={setFilter} masked={privacy.mask} />
      <MetricsPanel metrics={metrics} groupBy={groupBy} onGroupBy={setGroupBy} masked={privacy.mask} />

      <PrivacyPanel jobId={jobId} options={privacy} onOptions={setPrivacy} reloadKey={reloadKey}
        onScanned={() => setReloadKey((k) => k + 1)} />

      <div className="logs-body">
        <LogTable filter={applied} reloadKey={reloadKey} onTotal={onTotal} privacy={privacy}
          filtered={active.length > 0 || !!jobId || privacy.flaggedOnly} />
      </div>
    </div>
  );
}
