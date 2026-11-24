import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../../api";
import {
  banner, chooseCleanup, countSelection, describeSelection, getCleanup, groupTitle, hasPendingPods, outcomeSummary,
  pendingIds, podIds,
  type CleanupOutcome, type CleanupStatus,
} from "./cleanupApi";
import "./cleanup.css";

interface Props {
  /** "logout": the engineer is ending the session. "restart": leftovers found after a crash or restart. */
  context: "logout" | "restart";
  /** Close without signing out (logout) or decide later (restart). */
  onClose: () => void;
  /** Logout only: called when the engineer finishes and signs out. */
  onSignOut?: (leftoversUndecided: number) => void;
}

/** The cleanup choice: what is left on the system, per run, and what to do with it (requirement: known clean state). */
export default function CleanupDialog({ context, onClose, onSignOut }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const uid = useId();
  const [status, setStatus] = useState<CleanupStatus | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [review, setReview] = useState(context === "restart");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string[] | null>(null);   // ids waiting for "Yes, remove"
  const [outcomes, setOutcomes] = useState<CleanupOutcome[] | null>(null);
  const logout = context === "logout";

  const load = useCallback(async () => {
    setLoadError(false);
    try {
      const s = await getCleanup();
      setStatus(s);
      setPicked(new Set(pendingIds(s)));
    } catch {
      setLoadError(true);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof el.showModal === "function") { if (!el.open) el.showModal(); } else el.setAttribute("open", "");
  }, []);

  async function run(action: "remove" | "keep", ids: string[]) {
    if (!ids.length) return;
    setBusy(true);
    setError(null);
    setConfirm(null);
    try {
      const r = await chooseCleanup(action, context, ids);
      setOutcomes(r.outcomes);
      setStatus(r.status);
      setPicked(new Set(pendingIds(r.status)));
    } catch (e) {
      setError(e instanceof ApiError && e.status !== 0 ? e.message : "Can't reach the server.");
    } finally {
      setBusy(false);
    }
  }

  const toggle = (id: string) =>
    setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const b = status && banner(status);
  const pending = status?.state === "pending";
  const chosen = [...picked];
  const undecided = status?.pending_count ?? 0;
  const title = logout ? "Before you sign out" : "Leftovers from an interrupted run";

  return (
    <dialog ref={ref} className="cleanup-dialog" aria-labelledby={`${uid}-t`}
      onCancel={(e) => { e.preventDefault(); if (!busy) onClose(); }}>
      <h2 id={`${uid}-t`}>{title}</h2>
      <p className="muted">
        {logout
          ? "Choose what happens to the diagnostic tools Portrait IQ started on the system."
          : "The backend restarted while a run was going. These were left behind. Remove them or keep them; nothing is removed unless you choose it."}
      </p>

      {!status && !loadError && <p role="status">Checking what is left on the system…</p>}
      {loadError && (
        <div className="alert error" role="alert">
          <strong>Couldn't check what is left</strong>
          Check the connection to the server. <button type="button" className="btn ghost" onClick={() => void load()}>Try again</button>
        </div>
      )}

      {status && b && (
        <>
          <div className={`alert ${b.kind}`} role="status">
            <strong>{b.title}</strong>{b.body}
          </div>

          {status.groups.length > 0 && (
            <div className="cleanup-groups">
              {status.groups.map((g) => (
                <section key={g.job_id} aria-label={groupTitle(g)}>
                  <h3>{groupTitle(g)}</h3>
                  <ul>
                    {g.items.map((i) => (
                      <li key={i.id}>
                        <label>
                          {(review || !logout) && !i.kept && !i.active ? (
                            <input type="checkbox" checked={picked.has(i.id)} onChange={() => toggle(i.id)} disabled={busy} />
                          ) : null}
                          <span className="cleanup-label">{i.label}</span>
                          <span className="muted"> {i.detail}</span>
                          {i.kept && <span className="chip">Kept</span>}
                          {i.active && <span className="chip" title="Still running, so it can't be removed">In use</span>}
                        </label>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
              {status.groups.some((g) => g.items.some((i) => i.kind === "files" && !i.kept)) && (
                <p className="muted">
                  Removing collected files deletes that run's partial results from this machine. Its export
                  (the zip under Export) and logs already imported into the Logs page stay.
                </p>
              )}
            </div>
          )}

          {outcomes && <p role="status">{outcomeSummary(outcomes)}</p>}
          {outcomes?.filter((o) => o.result === "failed").map((o) => (
            <div className="alert error" role="alert" key={o.id}><strong>{o.id.split(":")[1]}</strong>{o.reason ?? "Could not be removed."}</div>
          ))}
          {error && <div className="alert error" role="alert"><strong>Nothing was changed</strong>{error}</div>}

          {confirm && (
            <div className="alert warn" role="alertdialog" aria-label="Confirm removal">
              <strong>Remove {describeSelection(countSelection(status, confirm))}?</strong>
              This can't be undone.
              <div className="cleanup-actions">
                <button type="button" className="btn" autoFocus disabled={busy} onClick={() => void run("remove", confirm)}>Yes, remove</button>
                <button type="button" className="btn ghost" disabled={busy} onClick={() => setConfirm(null)}>Back</button>
              </div>
            </div>
          )}

          {pending && !confirm && hasPendingPods(status) && (
            <p className="muted">
              Pods you keep still stop by themselves when their time limit runs out (about 20 minutes for a packet
              capture), and anything still inside them is lost then. Files on this machine stay until you remove them.
            </p>
          )}

          {!confirm && (
            <div className="cleanup-actions">
              {pending && logout && !review && (
                <>
                  <button type="button" className="btn" disabled={busy || !podIds(status).length} onClick={() => setConfirm(podIds(status))}>
                    Remove all deployments
                  </button>
                  <button type="button" className="btn ghost" disabled={busy} onClick={() => void run("keep", pendingIds(status))}>Keep for later</button>
                  <button type="button" className="btn ghost" disabled={busy} onClick={() => setReview(true)}>Review list</button>
                </>
              )}
              {pending && (review || !logout) && (
                <>
                  <button type="button" className="btn" disabled={busy || !chosen.length} onClick={() => setConfirm(chosen)}>
                    Remove selected ({chosen.length})
                  </button>
                  <button type="button" className="btn ghost" disabled={busy || !chosen.length} onClick={() => void run("keep", chosen)}>
                    Keep selected for later
                  </button>
                </>
              )}
              {logout ? (
                <>
                  <button type="button" className="btn" disabled={busy || pending} onClick={() => onSignOut?.(0)} autoFocus={!pending}>Sign out</button>
                  {pending && <button type="button" className="btn ghost" disabled={busy} onClick={() => onSignOut?.(undecided)}>Sign out anyway</button>}
                  <button type="button" className="btn ghost" disabled={busy} onClick={onClose}>Cancel</button>
                </>
              ) : (
                <button type="button" className="btn ghost" disabled={busy} onClick={onClose}>{pending ? "Decide later" : "Close"}</button>
              )}
            </div>
          )}
        </>
      )}
    </dialog>
  );
}
