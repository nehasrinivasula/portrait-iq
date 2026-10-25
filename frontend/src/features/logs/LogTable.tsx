import { useCallback, useEffect, useState } from "react";
import { searchEvents, type LogEvent, type Severity } from "./logsApi";

const SEV_ICON: Record<Severity, string> = { ERROR: "⛔", WARN: "⚠", INFO: "ℹ", DEBUG: "·" };

const time = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "medium" });

function Message({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <button className="msg-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}
      title={open ? "Show less" : "Show the full message"}>
      <span className="clip">{text}</span>
    </button>
  );
}

export default function LogTable({ jobId }: { jobId?: string }) {
  const [rows, setRows] = useState<LogEvent[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [more, setMore] = useState(false);

  const load = useCallback(async (next: string | null) => {
    next ? setMore(true) : setState("loading");
    try {
      const page = await searchEvents({ cursor: next, job_id: jobId });
      setRows((r) => (next ? [...r, ...page.items] : page.items));
      if (page.total !== null) setTotal(page.total);
      setCursor(page.next_cursor);
      setState("ready");
    } catch {
      setState("error");
    } finally {
      setMore(false);
    }
  }, [jobId]);

  useEffect(() => { load(null); }, [load]);

  if (state === "loading") return <p className="muted" role="status">Loading logs…</p>;
  if (state === "error" && rows.length === 0)
    return (
      <div className="alert error" role="alert">
        <strong>Couldn't load the logs</strong>
        Check the connection to the server, then try again.{" "}
        <button className="btn ghost" onClick={() => load(null)}>Try again</button>
      </div>
    );
  if (rows.length === 0)
    return <p className="muted" role="status">No logs yet. Pull logs for a time frame on the Get logs tab, then they appear here.</p>;

  return (
    <div className="table-wrap">
      <table className="log-table">
        <caption className="sr-only" style={{ position: "absolute", left: -9999 }}>Log events, newest first</caption>
        <thead>
          <tr><th scope="col">Time</th><th scope="col">Level</th><th scope="col">Device</th><th scope="col">Source</th><th scope="col">Process</th><th scope="col">Message</th></tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id}>
              <td className="mono"><time dateTime={e.timestamp} title={`${e.timestamp} (UTC)`}>{time.format(new Date(e.timestamp))}</time></td>
              <td><span className={`sev ${e.severity}`}><span aria-hidden="true">{SEV_ICON[e.severity]}</span>{e.severity}</span></td>
              <td>{e.device_id ?? "—"}</td>
              <td>{e.source}</td>
              <td>{e.process ?? "—"}</td>
              <td><Message text={e.message} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="table-foot">
        <span className="muted" role="status">Showing {rows.length}{total !== null ? ` of ${total}` : ""}</span>
        {state === "error" && <span className="error-text" role="alert">Couldn't load more.</span>}
        {cursor && <button className="btn ghost" disabled={more} onClick={() => load(cursor)}>{more ? "Loading…" : "Load more"}</button>}
      </div>
    </div>
  );
}
