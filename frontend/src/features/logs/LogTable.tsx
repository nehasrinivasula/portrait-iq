import { useCallback, useEffect, useRef, useState } from "react";
import { searchEvents, type LogEvent, type LogFilter } from "./logsApi";
import { categoryLabel, type PrivacyOptions } from "../privacy/privacyApi";
import "../privacy/privacy.css";

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

/** What the scan found in one event: kinds only, never the matched text. */
function Flags({ e }: { e: LogEvent }) {
  if (!e.privacy_scanned) return <span className="muted" title="Not scanned yet">not scanned</span>;
  const flags = e.sensitive ?? [];
  if (flags.length === 0) return <span className="muted">—</span>;
  const kinds = [...new Set(flags.map((f) => f.category))];
  return (
    <>
      {kinds.map((k) => {
        const conf = flags.find((f) => f.category === k)?.confidence ?? "medium";
        return <span key={k} className={`flag-tag ${conf}`} title={`${conf} confidence`}>{categoryLabel(k)}</span>;
      })}
    </>
  );
}

/** The scrolling part of the logs page. Filters change -> first page again. */
export default function LogTable({ filter, reloadKey, onTotal, filtered, privacy }: {
  filter: LogFilter; reloadKey: number; onTotal: (n: number | null) => void; filtered: boolean; privacy?: PrivacyOptions;
}) {
  const [rows, setRows] = useState<LogEvent[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [more, setMore] = useState(false);
  const latest = useRef(0);
  const shownMask = useRef(!!privacy?.mask);
  const scroller = useRef<HTMLDivElement>(null);

  const load = useCallback(async (next: string | null) => {
    const id = ++latest.current;
    // Rows loaded with the other mask setting must not stay on screen if this load fails.
    if (!next && shownMask.current !== !!privacy?.mask) { shownMask.current = !!privacy?.mask; setRows([]); setCursor(null); }
    next ? setMore(true) : setState("loading");
    try {
      const page = await searchEvents(filter, next, 100, privacy);
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
  }, [filter, onTotal, privacy]);

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
          <tr><th scope="col">Time</th><th scope="col">Level</th><th scope="col">Device</th><th scope="col">Source</th><th scope="col">Process</th><th scope="col">Message</th><th scope="col">PHI/PII</th></tr>
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
              <td className="flag-col"><Flags e={e} /></td>
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
