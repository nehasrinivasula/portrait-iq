import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api";
import { downloadPackage, getPackage, getPreview, startExport, type Package, type Preview } from "./exportApi";
import { formatBytes, headline, isPreparing, returnTarget } from "./exportView";
import { formatElapsed, parseServerTime } from "../plugins/progress";
import ExportSteps from "./ExportSteps";
import DownloadDialog from "./DownloadDialog";
import "./export.css";

const POLL_MS = 1000;

// One page per run (SCRUM-61): review what goes in the zip, watch it being
// prepared, then download it. Opened from Run diagnostics or Logs; ?from= says which.
export default function ExportPage() {
  const { jobId = "" } = useParams<{ jobId: string }>();
  const [params] = useSearchParams();
  const back = returnTarget(params.get("from"), jobId);

  const [preview, setPreview] = useState<Preview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pkg, setPkg] = useState<Package | null>(null);
  const [mask, setMask] = useState(true);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [lost, setLost] = useState(false);
  const [reviewing, setReviewing] = useState(true);       // false once an export is shown
  const [download, setDownload] = useState<"idle" | "running" | "error">("idle");
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);  // file name of a finished download: opens the dialog
  const [loadKey, setLoadKey] = useState(0);
  const resultRef = useRef<HTMLHeadingElement>(null);
  const announced = useRef<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoadError(null);
    getPreview(jobId)
      .then((p) => {
        if (!live) return;
        setPreview(p);
        setPkg(p.package);
        // An export still being prepared is shown straight away, e.g. after a reload.
        if (isPreparing(p.package)) setReviewing(false);
      })
      .catch((e) => { if (live) setLoadError(e instanceof ApiError && e.status === 404 ? "This run was not found." : "Couldn't load this run."); });
    return () => { live = false; };
  }, [jobId, loadKey]);

  // Poll while the zip is being prepared.
  useEffect(() => {
    if (!isPreparing(pkg)) return;
    const t = window.setTimeout(async () => {
      try {
        setPkg(await getPackage(jobId));
        setLost(false);
      } catch (e) {
        if (e instanceof ApiError && (e.status === 401 || e.status === 423)) return;
        setLost(true);
        setPkg((p) => (p ? { ...p } : p));    // re-arm the timer
      }
    }, POLL_MS);
    return () => window.clearTimeout(t);
  }, [pkg, jobId]);

  // When an export finishes, move focus to the result once; its alert announces it.
  useEffect(() => {
    if (!pkg || reviewing || isPreparing(pkg)) return;
    const key = `${pkg.job_id}:${pkg.started_at}`;
    if (announced.current === key) return;
    announced.current = key;
    resultRef.current?.focus();
  }, [pkg, reviewing]);

  const begin = useCallback(async () => {
    setStarting(true);
    setStartError(null);
    try {
      const p = await startExport(jobId, mask);
      announced.current = null;
      setPkg(p);
      setReviewing(false);
      setDownload("idle");
    } catch (e) {
      setStartError(e instanceof ApiError ? e.message : "Couldn't start the export.");
    } finally {
      setStarting(false);
    }
  }, [jobId, mask]);

  const save = useCallback(async () => {
    if (!pkg?.file_name) return;
    setDownload("running");
    setDownloadError(null);
    try {
      setSaved(await downloadPackage(jobId, pkg.file_name));
      setDownload("idle");
    } catch (e) {
      setDownload("error");
      setDownloadError(e instanceof ApiError ? e.message : "The download did not finish.");
    }
  }, [jobId, pkg]);

  if (loadError) {
    return (
      <>
        <Link to={back.to}>{back.label}</Link>
        <h1>Export run</h1>
        <div className="alert error" role="alert">
          <strong>{loadError}</strong>
          <button type="button" className="btn ghost" onClick={() => setLoadKey((k) => k + 1)}>Try again</button>
        </div>
      </>
    );
  }
  if (!preview) return <><h1>Export run</h1><p className="muted" role="status">Loading…</p></>;

  return (
    <div className="export-page">
      <Link to={back.to}>{back.label}</Link>
      <h1>Export run</h1>
      <p className="muted">Run <code>{jobId.slice(0, 8)}</code> · all its logs, diagnostic results and findings in one zip file.</p>

      {reviewing || !pkg
        ? <Review preview={preview} mask={mask} onMask={setMask} starting={starting} startError={startError}
            onStart={begin} onShowLast={() => setReviewing(false)} onDownloadLast={save} back={back} />
        : <Result pkg={pkg} lost={lost} resultRef={resultRef} download={download} downloadError={downloadError}
            onDownload={save} onAgain={() => { setReviewing(true); setLoadKey((k) => k + 1); }} back={back} />}

      {saved && pkg && <DownloadDialog pkg={pkg} fileName={saved} back={back} onClose={() => setSaved(null)} />}
    </div>
  );
}

interface ReviewProps {
  preview: Preview;
  mask: boolean;
  onMask: (m: boolean) => void;
  starting: boolean;
  startError: string | null;
  onStart: () => void;
  onShowLast: () => void;
  onDownloadLast: () => void;
  back: { to: string; label: string };
}

function Review({ preview: p, mask, onMask, starting, startError, onStart, onShowLast, onDownloadLast, back }: ReviewProps) {
  const last = p.package;
  const privacyOk = p.privacy.complete_for_packaging || p.events === 0;
  return (
    <>
      {!p.finished && (
        <div className="alert info" role="status">
          <strong>This run is still going</strong>
          You can export it once it has finished.
        </div>
      )}
      {last && last.state === "ready" && (
        <div className="alert info" role="status">
          <strong>Exported before</strong>
          {last.file_name} ({formatBytes(last.size_bytes)}). Exporting again replaces it.{" "}
          <span className="export-inline-actions">
            {last.downloadable && <button type="button" className="btn ghost" onClick={onDownloadLast}>Download it</button>}
            <button type="button" className="btn ghost" onClick={onShowLast}>See what it holds</button>
          </span>
        </div>
      )}

      <section className="card export-card" aria-labelledby="exp-what">
        <h2 id="exp-what">What goes in the zip</h2>
        <ul className="export-list">
          <li><strong>Logs</strong> {p.events ? `${p.events.toLocaleString()} log entries, in the standard format` : "This run has no logs"}</li>
          <li>
            <strong>Diagnostic results</strong>{" "}
            {p.tools.length === 0 ? "This run used no diagnostics" : `${p.tools.length} diagnostic${p.tools.length === 1 ? "" : "s"}`}
            {p.tools.length > 0 && (
              <ul className="export-tools">
                {p.tools.map((t) => (
                  <li key={t.tool} data-ok={t.ok}>
                    <span aria-hidden="true">{t.ok ? "✓" : "✗"}</span> {t.tool}{" "}
                    {t.ok ? <span className="muted">{t.files} file{t.files === 1 ? "" : "s"}</span>
                      : <span>Did not finish during the run: {t.error}. Its error is included.</span>}
                  </li>
                ))}
              </ul>
            )}
          </li>
          <li><strong>Findings</strong> {p.findings ? `${p.findings} from the analysis` : "None for this run"}</li>
          <li><strong>Audit trail and run details</strong> who ran and exported it, and when</li>
        </ul>
      </section>

      <section className="export-card" aria-label="PHI and PII">
        <div className={`alert ${privacyOk ? "ok" : "warn"}`} role="status">
          <strong>PHI / PII scan</strong>
          {p.events === 0 ? "No logs to scan."
            : privacyOk ? `All ${p.events.toLocaleString()} log entries scanned; ${p.privacy.flagged_events.toLocaleString()} flagged.`
            : "The scan is not complete. You can still export; the zip will say so."}
          {!privacyOk && (
            <>
              <ul className="export-list">{p.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
              <Link to={`/logs?job=${encodeURIComponent(p.job_id)}`}>Scan or check these logs on the Logs page</Link>
            </>
          )}
        </div>
        <div className="privacy-switches">
          <label>
            <input type="checkbox" checked={mask} onChange={(e) => onMask(e.target.checked)} aria-describedby="mask-hint" />
            Mask flagged PHI / PII (recommended)
          </label>
        </div>
        <p id="mask-hint" className="hint">
          Replaces flagged values with [REDACTED:kind], as the Logs page does. Packet captures can't be masked.
        </p>
        {!mask && p.privacy.flagged_events > 0 && (
          <div className="alert warn" role="status">
            <strong>Masking is off</strong>
            {p.privacy.flagged_events.toLocaleString()} flagged log entries will be in the zip as they are. The audit log records this.
          </div>
        )}
      </section>

      {startError && <div className="alert error" role="alert"><strong>Couldn't start the export</strong>{startError}</div>}
      <div className="export-actions">
        <button type="button" className="btn" disabled={!p.finished || starting} onClick={onStart}>
          {starting ? "Starting…" : "Export zip"}
        </button>
        <Link to={back.to} className="btn ghost">Cancel</Link>
      </div>
    </>
  );
}

interface ResultProps {
  pkg: Package;
  lost: boolean;
  resultRef: React.RefObject<HTMLHeadingElement | null>;
  download: "idle" | "running" | "error";
  downloadError: string | null;
  onDownload: () => void;
  onAgain: () => void;
  back: { to: string; label: string };
}

function Result({ pkg, lost, resultRef, download, downloadError, onDownload, onAgain, back }: ResultProps) {
  const preparing = isPreparing(pkg);
  const [kind, title, body] = headline(pkg);
  return (
    <>
      <h2 ref={resultRef} tabIndex={-1} className="export-result-head">{preparing ? "Preparing" : "Result"}</h2>
      <div className={`alert ${kind}`} role={kind === "error" ? "alert" : "status"} aria-live="polite">
        <strong>{title}</strong>
        {body}
      </div>
      {preparing && <ExportClock startedAt={pkg.started_at} />}
      {lost && <p className="muted" role="status">Lost contact with the server. Still trying; the export is not affected.</p>}

      {pkg.downloadable && (
        <section className="card export-card" aria-labelledby="exp-file">
          <h3 id="exp-file">The zip file</h3>
          <dl className="export-facts">
            <dt>File</dt><dd><code>{pkg.file_name}</code></dd>
            <dt>Size</dt><dd>{formatBytes(pkg.size_bytes)}</dd>
            <dt>SHA-256</dt><dd><code className="export-hash">{pkg.sha256}</code></dd>
            <dt>PHI / PII</dt><dd>{pkg.masked ? "Flagged values are masked." : "Not masked."}</dd>
          </dl>
          <div className="table-wrap export-table">
            <table className="log-table">
              <caption className="visually-hidden">Contents of the zip</caption>
              <thead><tr><th scope="col">In the zip</th><th scope="col">What it is</th><th scope="col">Files</th><th scope="col">Entries</th></tr></thead>
              <tbody>
                {pkg.contents.map((c) => (
                  <tr key={c.path}>
                    <td className="mono"><code>{c.path}</code></td>
                    <td>{c.description}{c.masked === false && <span className="muted"> (not masked)</span>}</td>
                    <td className="mono">{c.files}</td>
                    <td className="mono">{c.events === null ? "—" : c.events.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {pkg.warnings.length > 0 && (
            <div className="alert warn" role="status">
              <strong>Check before sharing</strong>
              <ul className="export-list">{pkg.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
          )}
        </section>
      )}

      <section className="export-card" aria-labelledby="exp-steps">
        <h3 id="exp-steps">Steps</h3>
        <ExportSteps steps={pkg.steps} live={preparing} />
      </section>

      {download === "error" && (
        <div className="alert error" role="alert"><strong>The download did not finish</strong>{downloadError} Try again.</div>
      )}
      <div className="export-actions">
        {pkg.downloadable && (
          <button type="button" className="btn" disabled={download === "running"} onClick={onDownload}>
            {download === "running" ? "Downloading…" : "Download zip"}
          </button>
        )}
        {!preparing && (
          <button type="button" className={pkg.downloadable ? "btn ghost" : "btn"} onClick={onAgain}>
            {pkg.state === "ready" ? "Export again" : "Try again"}
          </button>
        )}
        <Link to={back.to} className="btn ghost">{back.label}</Link>
      </div>
    </>
  );
}

function ExportClock({ startedAt }: { startedAt: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const start = parseServerTime(startedAt);
  if (start === null) return null;
  return <p className="muted run-clock" role="timer" aria-live="off">Preparing for {formatElapsed(now - start)}</p>;
}
