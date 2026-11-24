import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { searchEvents, type LogEvent, type LogFilter } from "./logsApi";
import { categoryLabel, type PrivacyOptions } from "../privacy/privacyApi";
import "../privacy/privacy.css";

const time = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "medium" });

/** A message is only a button when two lines cut it off; short ones are plain text with no arrow. */
function Message({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const [cut, setCut] = useState(false);
  const clip = useRef<HTMLSpanElement>(null);
  // Re-measured when the column width changes; while open, it stays a button so it can close.
  useLayoutEffect(() => {
    const el = clip.current;
    if (!el || open) return;
    const check = () => setCut(el.scrollHeight > el.clientHeight + 1);
    check();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text, open, cut]);
  if (!cut && !open) return <span ref={clip} className="clip">{text}</span>;
  return (
    <button className="msg-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}
      title={open ? "Show less" : "Show the full message"}>
      <span className="caret" aria-hidden="true" />
      <span ref={clip} className="clip">{text}</span>
    </button>
  );
}

/** What the scan found in one event: kinds only, never the matched text. */
function Flags({ e }: { e: LogEvent }) {
  const flags = e.sensitive ?? [];
  // A flag the server could not mask is not "nothing found".
  if (e.message.includes("[REDACTED:unreadable]"))
    return <span className="flag-tag medium" title="The flagged text could not be read, so it was hidden">hidden: unreadable</span>;
  // Flags win over the scanned marker: a masked row judged on the spot has flags but is not stored as scanned.
  if (flags.length === 0)
    return e.privacy_scanned ? <span className="muted" title="Scanned: nothing found">—</span>
      : <span className="muted" title="Not scanned yet">not scanned</span>;
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

const Head = () => (
  <thead>
    <tr>
      <th scope="col">Time</th><th scope="col">Level</th><th scope="col" className="msg-col">Message</th>
      <th scope="col">Device</th><th scope="col">Source</th><th scope="col">Process</th><th scope="col">PHI/PII</th>
    </tr>
  </thead>
);

/** First load: the table's shape at real row height, so nothing jumps when rows arrive. */
function Skeleton() {
  return (
    <div className="table-wrap" role="status" aria-busy="true" aria-label="Loading logs">
      <table className="log-table" aria-hidden="true">
        <Head />
        <tbody>
          {Array.from({ length: 10 }, (_, i) => (
            <tr key={i}>
              {[150, 60, 420 - (i % 3) * 90, 90, 70, 90, 60].map((w, j) => (
                <td key={j}><span className="skeleton-bar" style={{ width: w, animationDelay: `${i * 40}ms` }} /></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
  const [stale, setStale] = useState(false);       // a new search failed: the rows shown are from the old one
  const latest = useRef(0);
  const inflight = useRef<AbortController | null>(null);
  const shownMask = useRef(!!privacy?.mask);
  const scroller = useRef<HTMLDivElement>(null);

  const load = useCallback(async (next: string | null) => {
    const id = ++latest.current;
    // Rows loaded with the other mask setting must not stay on screen if this load fails.
    if (!next && shownMask.current !== !!privacy?.mask) { shownMask.current = !!privacy?.mask; setRows([]); setCursor(null); }
    next ? setMore(true) : setState("loading");
    inflight.current?.abort();          // the server stops work on a search nobody will read
    const ac = new AbortController();
    inflight.current = ac;
    try {
      const page = await searchEvents(filter, next, 100, privacy, ac.signal);
      if (id !== latest.current) return; // a newer search replaced this one
      setRows((r) => (next ? [...r, ...page.items] : page.items));
      setStale(false);
      if (page.total !== null) { setTotal(page.total); onTotal(page.total); }
      setCursor(page.next_cursor);
      setState("ready");
      if (!next) scroller.current?.scrollTo({ top: 0 });
    } catch {
      if (id === latest.current) { setState("error"); if (!next) setStale(true); }
    } finally {
      if (id === latest.current) setMore(false);
    }
  }, [filter, onTotal, privacy]);

  useEffect(() => { load(null); }, [load, reloadKey]);
  useEffect(() => () => inflight.current?.abort(), []);

  if (state === "loading" && rows.length === 0) return <Skeleton />;
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
      <p className="muted table-wrap table-note" role="status">
        {privacy?.flaggedOnly
          ? "No flagged events among the events scanned so far. Events that have not been scanned are not checked: see the scan panel above."
          : filtered ? "No logs match these filters. Loosen or clear them." : "No logs yet. Run diagnostics with logs included, or import log files."}
      </p>
    );

  const busy = state === "loading";
  return (
    <>
      {busy && <div className="busy-bar" aria-hidden="true" />}
      <div className="table-wrap" ref={scroller} tabIndex={0} aria-label="Log events" aria-busy={busy}>
        <table className="log-table">
          <Head />
          <tbody>
            {rows.map((e) => (
              <tr key={e.id} data-sev={e.severity}>
                <td className="nowrap"><time dateTime={e.timestamp} title={`${e.timestamp} (UTC)`}>{time.format(new Date(e.timestamp))}</time></td>
                <td><span className={`sev ${e.severity}`}>{e.severity}</span></td>
                <td className="msg-cell"><Message text={e.message} /></td>
                <td className="meta">{e.device_id ?? "—"}</td>
                <td className="meta">{e.source}</td>
                <td className="meta">{e.process ?? "—"}</td>
                <td className="flag-col"><Flags e={e} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="table-foot">
          <span className="muted" role="status">Showing {rows.length.toLocaleString()}{total !== null ? ` of ${total.toLocaleString()}` : ""}</span>
          {state === "error" && (
            <span className="text-error" role="alert">
              {stale ? "Couldn't update the results. These are from before your last change." : "Couldn't load more."}{" "}
              <button className="btn ghost" onClick={() => load(stale ? null : cursor)}>Try again</button>
            </span>
          )}
          {cursor && !stale && !busy && <button className="btn ghost" disabled={more} onClick={() => load(cursor)}>{more ? "Loading…" : "Load more"}</button>}
        </div>
      </div>
    </>
  );
}
