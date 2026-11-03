import { useCallback, useEffect, useRef, useState } from "react";
import { actionLabel, emptyAuditFilter, getAudit, latestOffset, PAGE_SIZE, shortSession, withRange, type AuditFilter, type AuditPage as Page } from "./auditApi";
import "./audit.css";

const time = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "medium" });

/** Who did what, and when. Newest entries first; filters apply when the form is submitted. */
export default function AuditPage() {
  const [draft, setDraft] = useState<AuditFilter>(emptyAuditFilter);
  const [filter, setFilter] = useState<AuditFilter>(emptyAuditFilter);
  const [offset, setOffset] = useState<number | null>(null);   // null = the latest page
  const [page, setPage] = useState<Page | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  const latest = useRef(0);

  const load = useCallback(async (f: AuditFilter, at: number | null) => {
    const id = ++latest.current;
    setState("loading");
    try {
      let from = at;
      if (from === null) {                       // learn the total first, then open the last page
        const head = await getAudit(f, 1, 0);
        if (id !== latest.current) return;       // a newer request replaced this one
        from = latestOffset(head.total, PAGE_SIZE);
      }
      const next = await getAudit(f, PAGE_SIZE, from);
      if (id !== latest.current) return;
      setPage(next);
      setOffset(from);
      setState("ready");
    } catch {
      if (id === latest.current) setState("error");
    }
  }, []);

  useEffect(() => { load(filter, null); }, [filter, load]);

  const set = (patch: Partial<AuditFilter>) => setDraft((d) => ({ ...d, ...patch }));
  const rows = page ? [...page.items].reverse() : [];       // newest first within the page
  const from = page && page.total > 0 ? page.offset + 1 : 0;
  const to = page ? page.offset + page.items.length : 0;

  return (
    <div className="audit-page">
      <h1>Audit log</h1>
      <p className="muted">
        A record of who did what and when. Entries never contain passwords, log text or search words.
        Opening this page is recorded too.
      </p>

      <form className="audit-form" onSubmit={(e) => { e.preventDefault(); setFilter(withRange({ ...draft })); }}>
        <div className="field">
          <label htmlFor="audit-actor">User</label>
          <input id="audit-actor" value={draft.actor} autoComplete="off" onChange={(e) => set({ actor: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="audit-action">Action</label>
          <input id="audit-action" value={draft.action} autoComplete="off" placeholder="e.g. login"
            onChange={(e) => set({ action: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="audit-session">Session</label>
          <input id="audit-session" value={draft.sessionId} autoComplete="off" placeholder="full session id"
            onChange={(e) => set({ sessionId: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="audit-range">When</label>
          <select id="audit-range" value={draft.range} onChange={(e) => set({ range: e.target.value as AuditFilter["range"] })}>
            <option value="any">Any time</option>
            <option value="hour">Last hour</option>
            <option value="day">Last 24 hours</option>
          </select>
        </div>
        <button type="submit" className="btn">Apply</button>
        <button type="button" className="btn ghost" onClick={() => { const e = emptyAuditFilter(); setDraft(e); setFilter(e); }}>Clear</button>
      </form>

      {state === "error" && (
        <div className="alert error" role="alert">
          <strong>Couldn't load the audit log</strong>
          Check the connection to the server, then try again.{" "}
          <button className="btn ghost" onClick={() => load(filter, offset)}>Try again</button>
        </div>
      )}
      {state === "loading" && !page && <p className="muted" role="status">Loading…</p>}
      {page && page.total === 0 && state !== "error" && <p className="muted" role="status">No entries match these filters.</p>}

      {page && page.total > 0 && (
        <>
          <div className="table-wrap" tabIndex={0} aria-label="Audit entries">
            <table className="log-table audit-table">
              <thead>
                <tr><th scope="col">Time</th><th scope="col">User</th><th scope="col">Action</th><th scope="col">Session</th><th scope="col">Details</th></tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr key={e.id}>
                    <td className="mono"><time dateTime={e.timestamp} title={`${e.timestamp} (UTC)`}>{time.format(new Date(e.timestamp))}</time></td>
                    <td>{e.actor}</td>
                    <td title={e.action}>{actionLabel(e.action)}</td>
                    <td className="mono" title={e.session_id ?? "No browser session (system work)"}>{shortSession(e.session_id)}</td>
                    <td className="audit-desc">{e.description || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="audit-foot">
            <span className="muted" role="status">Showing {from}–{to} of {page.total}</span>
            <button className="btn ghost" disabled={state === "loading" || page.offset === 0}
              onClick={() => load(filter, Math.max(0, page.offset - PAGE_SIZE))}>Older</button>
            <button className="btn ghost" disabled={state === "loading" || to >= page.total}
              onClick={() => load(filter, page.offset + PAGE_SIZE)}>Newer</button>
            <button className="btn ghost" disabled={state === "loading"} onClick={() => load(filter, null)}>Latest</button>
          </div>
        </>
      )}
    </div>
  );
}
