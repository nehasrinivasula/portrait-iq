import { useState } from "react";
import type { Package } from "./exportApi";
import { formatBytes } from "./exportView";
import { parseServerTime } from "../plugins/progress";

const when = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

const what = (p: Package) =>
  p.scope === "runs" ? `${p.runs.length} runs` : p.scope === "run" ? `Run ${p.job_id.slice(0, 8)}` : p.title;

const STATE: Record<string, string> = { preparing: "Preparing", ready: "Ready", failed: "Failed", interrupted: "Interrupted" };

interface Props {
  exports: Package[] | null;
  totalBytes: number;
  error: string | null;
  onDownload: (p: Package) => void;
  onDelete: (ids: string[]) => Promise<void>;
  busy: string | null;           // export id being downloaded
}

/** Every zip on this machine, newest first, kept until deleted. Deleting asks once, inline. */
export default function ExportHistory({ exports, totalBytes, error, onDownload, onDelete, busy }: Props) {
  const [confirm, setConfirm] = useState<string | null>(null);    // an export id, or "all"
  const [deleting, setDeleting] = useState(false);
  const removable = (exports ?? []).filter((p) => p.state !== "preparing");

  const remove = async (ids: string[]) => {
    setDeleting(true);
    try { await onDelete(ids); } finally { setDeleting(false); setConfirm(null); }
  };

  return (
    <section className="card export-card export-history" aria-labelledby="exp-history">
      <div className="section-head">
        <h2 id="exp-history">Exports on this machine</h2>
        {removable.length > 1 && (confirm === "all" ? (
          <span className="confirm-inline" role="group" aria-label="Confirm deleting every export">
            <span>Delete all {removable.length}? This can't be undone.</span>
            <button type="button" className="btn danger" disabled={deleting} onClick={() => remove(removable.map((p) => p.export_id))}>Delete all</button>
            <button type="button" className="btn ghost" onClick={() => setConfirm(null)}>Keep</button>
          </span>
        ) : (
          <button type="button" className="btn ghost" onClick={() => setConfirm("all")}>Delete all…</button>
        ))}
      </div>
      {error && <div className="alert error" role="alert"><strong>Couldn't list the exports</strong>{error}</div>}
      {exports === null && !error && <p className="muted" role="status">Loading…</p>}
      {exports?.length === 0 && <p className="muted">No exports yet.</p>}
      {exports && exports.length > 0 && (
        <>
          <p className="muted export-history-total">{exports.length} export{exports.length === 1 ? "" : "s"} · {formatBytes(totalBytes)} on disk</p>
          <ul className="export-history-list">
            {exports.map((p) => {
              const t = parseServerTime(p.started_at);
              return (
                <li key={p.export_id} data-state={p.state}>
                  <span className="export-history-main">
                    <strong>{what(p)}</strong>
                    <span className="muted">{t ? when.format(new Date(t)) : "—"} · {p.requested_by}</span>
                  </span>
                  <span className="export-history-facts">
                    <span className={`export-state ${p.state}`}>{STATE[p.state] ?? p.state}</span>
                    {p.size_bytes !== null && <span className="muted">{formatBytes(p.size_bytes)}</span>}
                    <span className="muted">{p.masked ? "Masked" : "Not masked"}</span>
                  </span>
                  <span className="export-history-actions">
                    {confirm === p.export_id ? (
                      <>
                        <span className="sr-inline">Delete this zip?</span>
                        <button type="button" className="btn danger" disabled={deleting} onClick={() => remove([p.export_id])}>Delete</button>
                        <button type="button" className="btn ghost" onClick={() => setConfirm(null)}>Keep</button>
                      </>
                    ) : (
                      <>
                        {p.downloadable && (
                          <button type="button" className="btn ghost" disabled={busy === p.export_id} onClick={() => onDownload(p)}
                            aria-label={`Download ${p.file_name ?? "zip"}`}>{busy === p.export_id ? "Downloading…" : "Download"}</button>
                        )}
                        {p.state !== "preparing" && (
                          <button type="button" className="btn ghost" onClick={() => setConfirm(p.export_id)}
                            aria-label={`Delete ${p.file_name ?? what(p)}`}>Delete</button>
                        )}
                      </>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
