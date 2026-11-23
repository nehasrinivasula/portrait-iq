import { useCallback, useEffect, useRef, useState } from "react";
import {
  ALL_LOGS, categoryLabel, followScan, loadPrivacy, privacyHeadline, privacyKind, scanScope,
  type PrivacyOptions, type PrivacyView,
} from "./privacyApi";
import { activeTasks, progressText } from "../tasks/tasksApi";
import "./privacy.css";

interface Props {
  jobId: string | null;            // the run being viewed, or null for all logs
  options: PrivacyOptions;
  onOptions: (o: PrivacyOptions) => void;
  reloadKey: number;
  onScanned: () => void;           // tell the table to reload after a scan
}

/** PHI/PII summary above the log table: was everything scanned, what was flagged, plus the two switches. */
export default function PrivacyPanel({ jobId, options, onOptions, reloadKey, onScanned }: Props) {
  const key = jobId ?? ALL_LOGS;
  const [view, setView] = useState<PrivacyView | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [scan, setScan] = useState<"idle" | "running" | "error">("idle");
  const [scanText, setScanText] = useState<string | null>(null);   // "Scanning events: x of y"
  const [localKey, setLocalKey] = useState(0);
  const busy = useRef(false);   // a scan is already being followed here

  // A scan error belongs to the run it happened on.
  useEffect(() => { setScan("idle"); }, [jobId]);

  useEffect(() => {
    let live = true;
    setState("loading");
    loadPrivacy(jobId)
      .then((v) => { if (live) { setView(v); setState("ready"); } })
      .catch(() => { if (live) setState("error"); });
    return () => { live = false; };
  }, [jobId, reloadKey, localKey]);

  // A report for a run that is no longer shown is never displayed.
  const current = view && view.key === key ? view : null;
  const report = current?.report ?? null;
  const shown = state === "ready" ? report : null;   // only a report that loaded without error is shown in detail
  const ready = shown !== null;

  // Run a scan (or follow one the server is already running) and refresh the counts and rows afterwards.
  const finishWith = useCallback(async (work: () => Promise<unknown>) => {
    busy.current = true;
    setScan("running");
    setScanText(null);
    let failed = false;
    try {
      await work();
    } catch {
      failed = true;
    }
    // Even a partial scan changed the counts and the rows, so read both again.
    busy.current = false;
    setScan(failed ? "error" : "idle");
    setScanText(null);
    setLocalKey((k) => k + 1);
    onScanned();
  }, [onScanned]);

  const runScan = useCallback(() => {
    if (!current) return Promise.resolve();
    return finishWith(() => scanScope(jobId, (t) => setScanText(progressText(t))));
  }, [current, jobId, finishWith]);

  // A scan started before a reload (or by someone else) is still running on the server: pick it up.
  useEffect(() => {
    let live = true;
    activeTasks("privacy_scan").then((tasks) => {
      const mine = tasks[0];
      if (!live || !mine || busy.current) return;
      void finishWith(() => followScan(mine.id, (t) => live && setScanText(progressText(t))));
    }).catch(() => { /* no resume; the button still works */ });
    return () => { live = false; };
  }, [jobId, finishWith]);

  const kind = state === "error" ? "error" : !report ? "info" : privacyKind(report);

  const headline = state === "error" ? "Couldn't read the scan report, so flagging and masking are unavailable."
    : report ? privacyHeadline(report) : "Checking…";

  // One line with the two switches always in reach; counts, the scan button and limits open below.
  return (
    <section className="privacy" aria-label="PHI and PII scan">
      <details className="strip">
        <summary>
          <span className={`dot ${kind}`} aria-hidden="true" />
          <span className="strip-title">PHI / PII</span>
          <span className="muted" role={state === "error" ? "alert" : "status"}>{headline}</span>
        </summary>
        <div className="strip-body">
          {shown && shown.events > 0 && (
            <>
              <ul className="privacy-stats" aria-label="Scan counts">
                <li><strong>{shown.events.toLocaleString()}</strong> events</li>
                <li><strong>{shown.flagged_events.toLocaleString()}</strong> flagged</li>
                <li title="IP and MAC addresses identify equipment. They are listed apart and are not counted as flagged.">
                  <strong>{shown.network_identifier_events.toLocaleString()}</strong> with IP / MAC addresses
                </li>
              </ul>
              {shown.by_category.length > 0 && (
                <ul className="privacy-cats" aria-label="What was flagged">
                  {shown.by_category.map((c) => (
                    <li key={c.value} className="privacy-cat">{categoryLabel(c.value)} <span className="muted">{c.count}</span></li>
                  ))}
                </ul>
              )}
            </>
          )}
          {shown && shown.limits.length > 0 && (
            <div>
              <strong>What this scan can and can't find</strong>
              <ul className="privacy-limits">{shown.limits.map((l) => <li key={l}>{l}</li>)}</ul>
            </div>
          )}
        </div>
      </details>

      <div className="privacy-actions">
        {state === "error" && (
          <button type="button" className="btn ghost" onClick={() => setLocalKey((k) => k + 1)}>Try again</button>
        )}
        {shown && shown.events > 0 && !shown.all_scanned && (
          <button type="button" className="btn ghost" disabled={scan === "running"} onClick={runScan}>
            {scan === "running" ? (scanText ?? "Scanning…") : `Scan ${shown.unscanned_events.toLocaleString()} events`}
          </button>
        )}
        <label>
          <input type="checkbox" checked={options.flaggedOnly} disabled={!ready && !options.flaggedOnly}
            onChange={(e) => onOptions({ ...options, flaggedOnly: e.target.checked })} />
          Flagged only
        </label>
        <label>
          <input type="checkbox" checked={options.mask} disabled={!ready && !options.mask}
            onChange={(e) => onOptions({ ...options, mask: e.target.checked })} />
          Mask flagged text
        </label>
      </div>

      {scan === "error" && (
        <div className="alert error" role="alert">
          <strong>The scan didn't finish</strong>
          Some events are still unscanned.{" "}
          <button type="button" className="btn ghost" onClick={runScan}>Try again</button>
        </div>
      )}
    </section>
  );
}
