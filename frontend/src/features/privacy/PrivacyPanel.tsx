import { useCallback, useEffect, useState } from "react";
import {
  ALL_LOGS, categoryLabel, loadPrivacy, privacyHeadline, privacyKind, scanScope,
  type PrivacyOptions, type PrivacyView,
} from "./privacyApi";
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
  const [localKey, setLocalKey] = useState(0);

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

  const runScan = useCallback(async () => {
    if (!current) return;
    setScan("running");
    let failed = false;
    try {
      await scanScope(jobId);
    } catch {
      failed = true;
    }
    // Even a partial scan changed the counts and the rows, so read both again.
    setScan(failed ? "error" : "idle");
    setLocalKey((k) => k + 1);
    onScanned();
  }, [current, jobId, onScanned]);

  const kind = state === "error" ? "error" : !report ? "info" : privacyKind(report);

  return (
    <section className="privacy" aria-label="PHI and PII scan">
      <div className={`alert ${kind}`} role={state === "error" ? "alert" : "status"}>
        <strong>PHI / PII scan{jobId ? "" : " (all logs)"}</strong>
        {state === "error" && (
          <>Couldn't read the scan report, so flagging and masking are unavailable.{" "}
            <button type="button" className="btn ghost" onClick={() => setLocalKey((k) => k + 1)}>Try again</button></>
        )}
        {state !== "error" && !report && "Checking…"}
        {state !== "error" && report && privacyHeadline(report)}
      </div>

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

          {!shown.all_scanned && (
            <p>
              <button type="button" className="btn" disabled={scan === "running"} onClick={runScan}>
                {scan === "running" ? "Scanning…" : `Scan ${shown.unscanned_events.toLocaleString()} events now`}
              </button>
            </p>
          )}
          {scan === "error" && (
            <div className="alert error" role="alert">
              <strong>The scan didn't finish</strong>
              Some events are still unscanned.{" "}
              <button type="button" className="btn ghost" onClick={runScan}>Try again</button>
            </div>
          )}
        </>
      )}

      <div className="privacy-switches">
        <label>
          <input type="checkbox" checked={options.flaggedOnly} disabled={!ready && !options.flaggedOnly}
            onChange={(e) => onOptions({ ...options, flaggedOnly: e.target.checked })} />
          Show flagged events only
        </label>
        <label>
          <input type="checkbox" checked={options.mask} disabled={!ready && !options.mask}
            onChange={(e) => onOptions({ ...options, mask: e.target.checked })} />
          Hide flagged text (mask)
        </label>
      </div>

      {shown && shown.limits.length > 0 && (
        <details className="privacy-limits">
          <summary>What this scan can and can't find</summary>
          <ul>{shown.limits.map((l) => <li key={l}>{l}</li>)}</ul>
        </details>
      )}
    </section>
  );
}
