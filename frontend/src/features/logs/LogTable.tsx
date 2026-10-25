import { useCallback, useEffect, useRef, useState } from "react";
import { searchEvents, type LogEvent, type LogFilter } from "./logsApi";

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

/** The scrolling part of the logs page. Filters change -> first page again. */
export default function LogTable({ filter, reloadKey, onTotal, filtered }: {
  filter: LogFilter; reloadKey: number; onTotal: (n: number | null) => void; filtered: boolean;
}) {
  const [rows, setRows] = useState<LogEvent[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [more, setMore] = useState(false);
  const latest = useRef(0);
  const scroller = useRef<HTMLDivElement>(null);

  const load = useCallback(async (next: string | null) => {
    const id = ++latest.current;
    next ? setMore(true) : setState("loading");
    try {
      const page = await searchEvents(filter, next);
      if (id !== latest.current) return; // a newer search replaced this one
      setRows((r) => (next ? [...r, ...page.items] : page.items));
      if (page.total !== null) { setTotal(page.total); onTotal(page.total); }
      setCursor(page.next_cursor);
      setState("ready");
      if (!next) scroller.current?.scrollTo({ top: 0 });
    } catch {
      if (id === latest.current) setState("error");
    } finally {
      if (id === latest.current) setMore(false);
    }
  }, [filter, onTotal]);

  useEffect(() => { load(null); }, [load, reloadKey]);

  if (state === "loading" && rows.length === 0) return <p className="muted" role="status">Loading logs…</p>;
  if (state === "error" && rows.length === 0)
    return (
      <div className="alert error" role="alert">
        <strong>Couldn't load the logs</strong>
        Check the connection to the server, then try again.{" "}
        <button className="btn ghost" onClick={() => load(null)}>Try again</button>
      </div>
    );
  if (rows.length === 0)
    return (
      <p className="muted" role="status">
        {filtered ? "No logs match these filters. Loosen or clear them." : "No logs yet. Run diagnostics with logs included, or import log files."}
      </p>
    );

  return (
    <div className="table-wrap" ref={scroller} tabIndex={0} aria-label="Log events">
      <table className="log-table">
        <thead>
          <tr><th scope="col">Time</th><th scope="col">Level</th><th scope="col">Device</th><th scope="col">Source</th><th scope="col">Process</th><th scope="col">Message</th></tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id}>
              <td className="mono"><time dateTime={e.timestamp} title={`${e.timestamp} (UTC)`}>{time.format(new Date(e.timestamp))}</time></td>
              <td><span className={`sev ${e.severity}`}>{e.severity}</span></td>
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
