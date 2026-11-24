import { useEffect, useState } from "react";
import Dropdown, { type DropdownOption } from "../../ui/Dropdown";
import { getLogSetFiles, IMPORTED, listLogSets, type LogSet, type LogSetFile } from "./logsApi";
import { parseServerTime, runHeadline } from "../plugins/progress";

// Which logs to look at: every log on this machine, the files imported without
// a run, or one run, and optionally one file inside it. Sits above the data it
// controls, so the table, insights and filters below all follow it.

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

export default function SourcePicker({ selected, file, onSelect, reloadKey }: Props) {
  const [sets, setSets] = useState<LogSet[] | null>(null);
  const [files, setFiles] = useState<LogSetFile[] | "error" | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let live = true;
    listLogSets()
      .then((s) => { if (live) { setSets(s); setError(false); } })
      .catch(() => { if (live) setError(true); });
    return () => { live = false; };
  }, [reloadKey, retry]);

  useEffect(() => {
    if (selected === null) { setFiles(null); return; }
    let live = true;
    setFiles(null);
    getLogSetFiles(selected).then((f) => live && setFiles(f)).catch(() => live && setFiles("error"));
    return () => { live = false; };
  }, [selected, reloadKey]);

  if (error) {
    return (
      <div className="alert error source-picker" role="alert">
        <strong>Couldn't list the log sources.</strong>{" "}
        <button type="button" className="btn ghost" onClick={() => setRetry((n) => n + 1)}>Try again</button>
      </div>
    );
  }

  const empty = (sets ?? []).filter((s) => s.events === 0 && s.job_id !== selected).length;
  const sourceOptions: DropdownOption[] = (sets ?? []).filter((s) => s.events > 0 || s.job_id === selected).map((s) => {
    const [, status] = s.status && s.status !== "done" ? runHeadline(s.status) : [null, null];
    const tools = s.job_id === IMPORTED ? "" : s.tools.join(", ");
    return {
      value: s.job_id,
      label: [logSetTitle(s, s.job_id), tools, status].filter(Boolean).join(" · "),
      count: s.events,
    };
  });
  const total = (sets ?? []).reduce((n, s) => n + s.events, 0);
  const fileOptions: DropdownOption[] = Array.isArray(files)
    ? files.map((f) => ({ value: f.source_file, label: fileName(f.source_file), count: f.events_in_store ?? 0 }))
    : [];

  return (
    <section className="source-picker" aria-label="Choose which logs to view">
      <Dropdown id="logs-source" label="Show logs from" value={selected ?? ""} onChange={(v) => onSelect(v || null, null)}
        options={sourceOptions} anyLabel={`All logs${sets ? ` (${total.toLocaleString()} entries)` : ""}`}
        description={`Every run and import that has logs. Pick one to look at just its logs.${empty ? ` ${empty} run${empty === 1 ? "" : "s"} with no logs ${empty === 1 ? "is" : "are"} hidden.` : ""}`}
        emptyText={sets === null ? "Loading…" : "No runs or imports yet. Use Import log files."} />
      <Dropdown id="logs-file" label="File" value={file ?? ""} onChange={(v) => onSelect(selected, v || null)}
        options={fileOptions} anyLabel={selected === null ? "Pick a run or import first" : "All files"} disabled={selected === null}
        emptyText={files === "error" ? "Couldn't list the files." : files === null ? "Loading…" : "No log files."}
        description="One log file inside the chosen source." />
    </section>
  );
}
