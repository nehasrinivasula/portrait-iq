import { useCallback, useEffect, useState } from "react";
import {
  categoryLabel, getPrivacyReport, privacyHeadline, scanCollection, UNASSIGNED,
  type PrivacyOptions, type PrivacyReport,
} from "./privacyApi";
import "./privacy.css";

interface Props {
  jobId: string | null;            // the run being viewed, or null for logs not tied to a run
  options: PrivacyOptions;
  onOptions: (o: PrivacyOptions) => void;
  reloadKey: number;
  onScanned: () => void;           // tell the table to reload after a scan
}

/** PHI/PII summary above the log table: was everything scanned, what was flagged, plus the two switches. */
export default function PrivacyPanel({ jobId, options, onOptions, reloadKey, onScanned }: Props) {
  const collection = jobId ?? UNASSIGNED;
  const [report, setReport] = useState<PrivacyReport | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [scanning, setScanning] = useState(false);
  const [localKey, setLocalKey] = useState(0);

  useEffect(() => {
    let live = true;
    setState("loading");
    getPrivacyReport(collection)
      .then((r) => { if (live) { setReport(r); setState("ready"); } })
      .catch(() => { if (live) setState("error"); });
    return () => { live = false; };
  }, [collection, reloadKey, localKey]);

  const scan = useCallback(async () => {
    setScanning(true);
    try {
      await scanCollection(collection);
      setLocalKey((k) => k + 1);
      onScanned();
    } catch {
      setState("error");
    } finally {
      setScanning(false);
    }
  }, [collection, onScanned]);

  const kind = !report ? "info" : !report.all_scanned ? "warn" : report.flagged_events > 0 ? "warn" : "ok";

  return (
    <section className="privacy" aria-label="PHI and PII scan">
      <div className={`alert ${state === "error" ? "error" : kind}`} role={state === "error" ? "alert" : "status"}>
        <strong>PHI / PII scan{jobId ? "" : " (logs not tied to a run)"}</strong>
        {state === "loading" && !report && "Checking…"}
        {state === "error" && "Couldn't read the scan report. Check the connection to the server."}
        {state !== "error" && report && privacyHeadline(report)}
      </div>

      {report && state !== "error" && report.events > 0 && (
        <>
          <ul className="privacy-stats" aria-label="Scan counts">
            <li><strong>{report.events.toLocaleString()}</strong> events</li>
            <li><strong>{report.flagged_events.toLocaleString()}</strong> flagged</li>
            <li title="IP and MAC addresses identify equipment. They are listed apart and are not counted as flagged.">
              <strong>{report.network_identifier_events.toLocaleString()}</strong> with IP / MAC addresses
            </li>
          </ul>

          {report.by_category.length > 0 && (
            <ul className="privacy-cats" aria-label="What was flagged">
              {report.by_category.map((c) => (
                <li key={c.value} className="privacy-cat">{categoryLabel(c.value)} <span className="muted">{c.count}</span></li>
              ))}
            </ul>
          )}

          {!report.all_scanned && (
            <p>
              <button type="button" className="btn" disabled={scanning} onClick={scan}>
                {scanning ? "Scanning…" : `Scan ${report.unscanned_events.toLocaleString()} events now`}
              </button>
            </p>
          )}
        </>
      )}

      <div className="privacy-switches">
        <label>
          <input type="checkbox" checked={options.flaggedOnly}
            onChange={(e) => onOptions({ ...options, flaggedOnly: e.target.checked })} />
          Show flagged events only
        </label>
        <label>
          <input type="checkbox" checked={options.mask}
            onChange={(e) => onOptions({ ...options, mask: e.target.checked })} />
          Hide flagged text (mask)
        </label>
      </div>

      {report && report.limits.length > 0 && (
        <details className="privacy-limits">
          <summary>What this scan can and can't find</summary>
          <ul>{report.limits.map((l) => <li key={l}>{l}</li>)}</ul>
        </details>
      )}
    </section>
  );
}
