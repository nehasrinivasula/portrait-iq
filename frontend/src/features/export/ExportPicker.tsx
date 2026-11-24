import { useId, useState } from "react";
import * as RadioGroup from "@radix-ui/react-radio-group";
import { IMPORTED, type LogSet } from "../logs/logsApi";
import { parseServerTime, runHeadline } from "../plugins/progress";
import type { Selection } from "./exportApi";

const SHOWN = 8;   // the most recent runs; the rest are one click away
const FINISHED = new Set(["done", "partial", "failed", "cleanup_failed", "interrupted"]);
const when = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export const runWhen = (s: LogSet) => {
  const t = parseServerTime(s.created_at);
  return t === null ? `Run ${s.job_id.slice(0, 8)}` : when.format(new Date(t));
};

interface Props {
  sets: LogSet[] | null;
  selection: Selection;
  onSelection: (s: Selection) => void;
  /** Run ids with at least one export on this machine. */
  exported: Set<string>;
  toolName: (t: string) => string;
}

/** Step 1 of the Export page: runs (one or several, ticked), the imported files, or every log. */
export default function ExportPicker({ sets, selection, onSelection, exported, toolName }: Props) {
  const uid = useId();
  const [showAll, setShowAll] = useState(false);
  const runs = (sets ?? []).filter((s) => s.job_id !== IMPORTED);
  const imported = (sets ?? []).find((s) => s.job_id === IMPORTED);
  const total = (sets ?? []).reduce((n, s) => n + s.events, 0);
  const picked = selection.kind === "runs" ? selection.runs : [];
  const finished = runs.filter((r) => FINISHED.has(r.status ?? ""));
  const allOn = finished.length > 0 && finished.every((r) => picked.includes(r.job_id));
  const toggle = (id: string, on: boolean) =>
    onSelection({ kind: "runs", runs: on ? [...picked, id] : picked.filter((x) => x !== id) });

  return (
    <div className="export-picker">
      <RadioGroup.Root className="segmented" aria-label="What to export" value={selection.kind}
        onValueChange={(v) => onSelection(v === "runs" ? { kind: "runs", runs: picked } : { kind: v as "imported" | "all" })}>
        <RadioGroup.Item value="runs" className="seg"><span>Runs</span></RadioGroup.Item>
        <RadioGroup.Item value="imported" className="seg" disabled={!imported?.events}>
          <span>Imported files{imported?.events ? ` · ${imported.events.toLocaleString()}` : ""}</span>
        </RadioGroup.Item>
        <RadioGroup.Item value="all" className="seg"><span>All logs{sets ? ` · ${total.toLocaleString()}` : ""}</span></RadioGroup.Item>
      </RadioGroup.Root>

      {selection.kind === "runs" && (
        <>
          <div className="export-runs-head">
            <span className="muted">{picked.length ? `${picked.length} of ${finished.length} picked` : "Tick one run, or several to put them in one zip."}</span>
            {finished.length > 1 && (
              <button type="button" className="btn ghost" onClick={() => onSelection({ kind: "runs", runs: allOn ? [] : finished.map((r) => r.job_id) })}>
                {allOn ? "Clear" : "Select all"}
              </button>
            )}
          </div>
          {sets === null && <p className="muted" role="status">Loading runs…</p>}
          {sets !== null && runs.length === 0 && <p className="muted">No runs yet. Run diagnostics first, or export the imported files or all logs.</p>}
          <ul className="export-runs" aria-label="Runs">
            {runs.filter((r, i) => showAll || i < SHOWN || picked.includes(r.job_id)).map((r) => {
              const done = FINISHED.has(r.status ?? "");
              const on = picked.includes(r.job_id);
              const [kind, status] = runHeadline(r.status ?? "");
              return (
                <li key={r.job_id} className={on ? "on" : undefined}>
                  <label htmlFor={`${uid}-${r.job_id}`}>
                    <input id={`${uid}-${r.job_id}`} type="checkbox" checked={on} disabled={!done}
                      onChange={(e) => toggle(r.job_id, e.target.checked)} />
                    <span className="export-run-main">
                      <strong>{runWhen(r)}</strong>
                      <span className="muted">{r.tools.length ? r.tools.map(toolName).join(", ") : "Logs only"}</span>
                    </span>
                    <span className="export-run-facts">
                      <span className={`run-status ${kind}`}>{done ? status : "Still running"}</span>
                      <span className="muted">{r.events.toLocaleString()} entries</span>
                      {exported.has(r.job_id) && <span className="override-badge">Exported</span>}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {runs.length > SHOWN && (
            <button type="button" className="btn ghost export-more" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
              {showAll ? "Show recent runs only" : `Show all ${runs.length} runs`}
            </button>
          )}
        </>
      )}
    </div>
  );
}
