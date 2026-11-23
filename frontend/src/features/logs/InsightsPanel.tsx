import type { Facets, FacetValue, LogFilter } from "./logsApi";

// What is in the logs being viewed (this source, this file, these filters), so
// the engineer sees the shape of the data before reading it: how many entries,
// what span, how many are errors, and what repeats. Every list can hide a value,
// which is how noise is switched off. Counts follow every active filter.

interface Props {
  facets: Facets | null;
  filter: LogFilter;
  onFilter: (f: LogFilter) => void;
  masked: boolean;
}

const span = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });
const n = (v: number) => v.toLocaleString();
const pct = (part: number, whole: number) => (whole ? Math.max(part ? 1 : 0, Math.round((part / whole) * 100)) : 0);
const cut = (m: string) => (m.length > 90 ? `${m.slice(0, 90)}…` : m);

export default function InsightsPanel({ facets, filter, onFilter, masked }: Props) {
  if (!facets) return <div className="strip strip-row muted" role="status"><span className="strip-title">Insights</span> Loading…</div>;
  if (facets.total === 0) return null;   // the table already says nothing matches

  const level = (l: string) => facets.severities.find((s) => s.value === l)?.count ?? 0;
  const add = (key: "hideCategories" | "hideProcesses" | "hideMessages", v: string) =>
    onFilter({ ...filter, [key]: [...filter[key], v] });
  const list = (title: string, rows: FacetValue[] | undefined, key: "hideCategories" | "hideProcesses" | "hideMessages",
    show: (v: string) => string = (v) => v) => {
    const shown = (rows ?? []).filter((r) => r.value).slice(0, 3);
    if (shown.length === 0) return null;
    const max = shown[0].count;
    return (
      <section className="insight-list" aria-label={title}>
        <h3>{title}</h3>
        <ul>
          {shown.map((r) => (
            <li key={r.value}>
              <span className="insight-bar" style={{ width: `${pct(r.count, max)}%` }} aria-hidden="true" />
              <span className="insight-name" title={r.value!}>{show(r.value!)}</span>
              <span className="insight-count">{n(r.count)}×</span>
              <button type="button" className="btn ghost insight-hide" onClick={() => add(key, r.value!)}
                aria-label={`Hide ${cut(r.value!)}`}>Hide</button>
            </li>
          ))}
        </ul>
      </section>
    );
  };

  const errors = level("ERROR"), warns = level("WARN");
  return (
    <details className="strip">
      <summary>
        <span className="strip-title">Insights</span>
        <span className="muted">{n(facets.total)} entries
          {errors > 0 && <> · <strong className="text-error">{n(errors)} error{errors === 1 ? "" : "s"}</strong></>}
          {warns > 0 && <> · {n(warns)} warning{warns === 1 ? "" : "s"}</>}
        </span>
      </summary>
      <div className="strip-body">
        <dl className="insight-facts">
          {facets.first_timestamp && facets.last_timestamp && (
            <div><dt>Covers</dt><dd>{span.format(new Date(facets.first_timestamp))} to {span.format(new Date(facets.last_timestamp))}</dd></div>
          )}
          <div><dt>Devices</dt><dd>{masked ? "Hidden while masking" : n(facets.distinct_devices)}</dd></div>
        </dl>
        {masked ? (
          <p className="muted">Repeated messages, categories and processes are hidden while PHI / PII masking is on.</p>
        ) : (
          <div className="insight-lists">
            {list("Most repeated messages", facets.repeated_messages, "hideMessages", cut)}
            {list("Busiest categories", facets.categories, "hideCategories")}
            {list("Busiest processes", facets.processes, "hideProcesses")}
          </div>
        )}
      </div>
    </details>
  );
}
