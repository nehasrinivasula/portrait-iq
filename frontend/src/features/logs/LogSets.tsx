import { useEffect, useState } from "react";
import { getLogSetFiles, IMPORTED, listLogSets, type LogSet, type LogSetFile } from "./logsApi";
import { parseServerTime, runHeadline } from "../plugins/progress";

// Every set of logs on this machine, like folders: all logs, each run, and the
// files imported without a run. Open a set to see its files; pick a set or a
// file to show just those logs.

interface Props {
  selected: string | null;            // a run id, IMPORTED, or null for all logs
  file: string | null;
  onSelect: (set: string | null, file: string | null) => void;
  reloadKey: number;
}

const when = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function logSetTitle(set: LogSet | undefined, id: string | null): string {
  if (id === null) return "All logs";
  if (id === IMPORTED) return "Imported files";
  const t = parseServerTime(set?.created_at);
  return t === null ? `Run ${id.slice(0, 8)}` : `Run, ${when.format(new Date(t))}`;
}

/** Stored names carry where the file was staged (local/<hash>/name.csv): show the name. */
export const fileName = (path: string) => path.split("/").pop() || path;

const entryCount = (n: number) => `${n.toLocaleString()} ${n === 1 ? "entry" : "entries"}`;

export default function LogSets({ selected, file, onSelect, reloadKey }: Props) {
  const [sets, setSets] = useState<LogSet[] | null>(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>(() => (selected ? { [selected]: true } : {}));
  const [files, setFiles] = useState<Record<string, LogSetFile[] | "error">>({});
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let live = true;
    listLogSets()
      .then((s) => { if (live) { setSets(s); setError(false); } })
      .catch(() => { if (live) setError(true); });
    setFiles({});            // file lists may have changed (an import)
    return () => { live = false; };
  }, [reloadKey, retry]);

  // Load a set's files the first time it is opened.
  useEffect(() => {
    for (const id of Object.keys(open)) {
      if (!open[id] || files[id]) continue;
      getLogSetFiles(id)
        .then((f) => setFiles((m) => ({ ...m, [id]: f })))
        .catch(() => setFiles((m) => ({ ...m, [id]: "error" })));
    }
  }, [open, files]);

  if (error) {
    return (
      <nav className="log-sets" aria-label="Log sets">
        <div className="alert error" role="alert"><strong>Couldn't list the log sets</strong>
          <button type="button" className="btn ghost" onClick={() => setRetry((n) => n + 1)}>Try again</button>
        </div>
      </nav>
    );
  }

  const runs = (sets ?? []).filter((s) => s.job_id !== IMPORTED);
  const imported = (sets ?? []).find((s) => s.job_id === IMPORTED);
  const total = (sets ?? []).reduce((n, s) => n + s.events, 0);

  const row = (id: string | null, title: string, sub: string, set?: LogSet) => {
    const isOpen = id !== null && !!open[id];
    const current = selected === id && !file;
    const [kind, status] = set && set.status && set.status !== "done" ? runHeadline(set.status) : [null, null];
    const list = id !== null ? files[id] : undefined;
    return (
      <li key={id ?? "all"}>
        <div className="log-set-row" data-current={current || undefined}>
          {id !== null ? (
            <button type="button" className="log-set-toggle" aria-expanded={isOpen}
              aria-label={`${isOpen ? "Hide" : "Show"} files in ${title}`}
              onClick={() => setOpen((o) => ({ ...o, [id]: !o[id] }))}>
              <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
            </button>
          ) : <span className="log-set-toggle" aria-hidden="true" />}
          <button type="button" className="log-set-pick" aria-current={current ? "true" : undefined}
            onClick={() => onSelect(id, null)}>
            <span className="log-set-title">{title}</span>
            <span className="log-set-sub">{sub}</span>
            {status && <span className={`log-set-tag ${kind}`}>{status}</span>}
          </button>
        </div>
        {isOpen && (
          <ul className="log-set-files">
            {list === undefined && <li className="muted">Loading…</li>}
            {list === "error" && <li className="muted">Couldn't list the files.</li>}
            {Array.isArray(list) && list.length === 0 && <li className="muted">No log files.</li>}
            {Array.isArray(list) && set && (() => {
              const untracked = set.events - list.reduce((n, f) => n + (f.events_in_store ?? 0), 0);
              return untracked > 0 ? <li className="muted">{entryCount(untracked)} without a file record</li> : null;
            })()}
            {Array.isArray(list) && list.map((f) => {
              const here = selected === id && file === f.source_file;
              return (
                <li key={f.source_file}>
                  <button type="button" className="log-set-file" aria-current={here ? "true" : undefined}
                    title={f.source_file} aria-label={`${fileName(f.source_file)}, ${entryCount(f.events_in_store ?? 0)}`}
                    data-current={here || undefined} onClick={() => onSelect(id, f.source_file)}>
                    <span className="log-set-title">{fileName(f.source_file)}</span>
                    <span className="log-set-sub">{entryCount(f.events_in_store ?? 0)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </li>
    );
  };

  const entries = (n: number) => (n ? entryCount(n) : "No logs");
  return (
    <nav className="log-sets" aria-label="Log sets">
      <h2 className="log-sets-head">Log sets</h2>
      {sets === null ? <p className="muted" role="status">Loading…</p> : (
        <>
          <ul className="log-set-list">{row(null, "All logs", entries(total))}</ul>
          <h3 className="log-sets-group">Imported</h3>
          {imported
            ? <ul className="log-set-list">{row(IMPORTED, "Imported files", entries(imported.events), imported)}</ul>
            : <p className="muted log-sets-none">None yet. Use Import log files above.</p>}
          <h3 className="log-sets-group">Runs</h3>
          {runs.length === 0
            ? <p className="muted log-sets-none">No runs yet.</p>
            : <ul className="log-set-list">{runs.map((r) => row(r.job_id, logSetTitle(r, r.job_id),
                [r.tools.join(", ") || null, entries(r.events)].filter(Boolean).join(" · "), r))}</ul>}
        </>
      )}
    </nav>
  );
}
