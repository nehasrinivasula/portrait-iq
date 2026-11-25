import { useState } from "react";
import type { Metrics, MetricsGroup } from "./logsApi";
import { LEVELS, bucketLabel, percent, segmentHeights } from "./metrics";

// Plain counts for the logs being viewed (A-4): how many lines of each level, how they
// spread over the period, and which service wrote the most errors. Follows every filter.

interface Props {
  metrics: Metrics | null;
  groupBy: MetricsGroup;
  onGroupBy: (g: MetricsGroup) => void;
  masked: boolean;
}

const n = (v: number) => v.toLocaleString();
const GROUP_LABEL: Record<MetricsGroup, string> = {
  process: "Service (process)", category: "Category", source: "Source", device: "Device", component: "Component",
};
const LEVEL_LABEL: Record<string, string> = { ERROR: "Errors", WARN: "Warnings", INFO: "Info", DEBUG: "Debug" };

export default function MetricsPanel({ metrics, groupBy, onGroupBy, masked }: Props) {
  const [open, setOpen] = useState(false);
  if (!metrics) return <div className="strip strip-row muted" role="status"><span className="strip-title">Metrics</span> Loading…</div>;
  if (metrics.total === 0) return null;

  const busiest = Math.max(1, ...metrics.buckets.map((b) => b.total));
  const rows = metrics.other ? [...metrics.groups, metrics.other] : metrics.groups;
  return (
    <details className="strip" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <span className="strip-title">Metrics</span>
        <span className="muted">{LEVELS.filter((l) => l === "ERROR" || l === "WARN").map((l) =>
          `${n(metrics.severities[l] ?? 0)} ${LEVEL_LABEL[l].toLowerCase()}`).join(" · ")}</span>
      </summary>
      <div className="strip-body">
        <div className="metric-tiles">
          {LEVELS.map((l) => (
            <div key={l} className={`metric-tile metric-${l.toLowerCase()}`}>
              <span className="metric-n">{n(metrics.severities[l] ?? 0)}</span>
              <span className="metric-l">{LEVEL_LABEL[l]} · {percent(metrics.severities[l] ?? 0, metrics.total)}%</span>
            </div>
          ))}
        </div>

        {metrics.bucket && metrics.buckets.length > 0 && (
          <section aria-label="Entries over time">
            <h3>Entries per {metrics.bucket}</h3>
            <div className="metric-chart" role="img"
              aria-label={`${metrics.buckets.length} ${metrics.bucket}s, busiest has ${n(busiest)} entries`}>
              {metrics.buckets.map((b) => {
                const h = segmentHeights(b.counts, busiest);
                return (
                  <div key={b.start} className="metric-bar"
                    title={`${bucketLabel(b.start, metrics.bucket!)}: ${LEVELS.map((l) => `${b.counts[l] ?? 0} ${l}`).join(", ")}`}>
                    {LEVELS.map((l) => <span key={l} className={`metric-seg metric-${l.toLowerCase()}`} style={{ height: `${h[l]}%` }} />)}
                  </div>
                );
              })}
            </div>
            <div className="metric-axis muted">
              <span>{bucketLabel(metrics.buckets[0].start, metrics.bucket)}</span>
              <span>{bucketLabel(metrics.buckets[metrics.buckets.length - 1].start, metrics.bucket)}</span>
            </div>
          </section>
        )}

        <section aria-label="Breakdown">
          <h3>
            Errors and warnings by{" "}
            <select value={groupBy} onChange={(e) => onGroupBy(e.target.value as MetricsGroup)} aria-label="Group by">
              {(Object.keys(GROUP_LABEL) as MetricsGroup[]).map((g) => <option key={g} value={g}>{GROUP_LABEL[g]}</option>)}
            </select>
          </h3>
          {metrics.withheld || (masked && groupBy !== "source" && metrics.groups.length === 0) ? (
            <p className="muted">Hidden while PHI / PII masking is on. Choose Source, or turn masking off.</p>
          ) : (
            <table className="metric-table">
              <thead><tr><th>{GROUP_LABEL[groupBy]}</th><th>Errors</th><th>Warnings</th><th>All</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.value ?? `other-${i}`}>
                    <td>{r === metrics.other ? `Other (${n(metrics.groups_total - metrics.groups.length)} more)` : r.value ?? "(none)"}</td>
                    <td>{n(r.counts.ERROR ?? 0)}</td><td>{n(r.counts.WARN ?? 0)}</td><td>{n(r.total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </details>
  );
}
