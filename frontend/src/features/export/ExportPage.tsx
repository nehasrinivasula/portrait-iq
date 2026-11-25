import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api";
import {
  deleteExport, downloadPackage, getExport, getPreview, listExports, selectionScope, startExport,
  type Package, type Preview, type Selection,
} from "./exportApi";
import { formatBytes, headline, isPreparing, logsLink, returnTarget } from "./exportView";
import { formatElapsed, parseServerTime } from "../plugins/progress";
import { listPlugins } from "../plugins/api";
import { IMPORTED, listLogSets, type LogSet } from "../logs/logsApi";
import { pollDelay } from "../../ui/stepModel";
import ExportSteps from "./ExportSteps";
import ExportPicker, { runWhen } from "./ExportPicker";
import ExportHistory from "./ExportHistory";
import DownloadDialog from "./DownloadDialog";
import "./export.css";

/** What the page opens with: the run, imported files or all logs it was opened for; runs with nothing ticked from the nav. */
function initialSelection(jobId: string | undefined): Selection {
  if (!jobId) return { kind: "runs", runs: [] };
  if (jobId === "all") return { kind: "all" };
  if (jobId === IMPORTED) return { kind: "imported" };
  return { kind: "runs", runs: [jobId] };
}

// Export (SCRUM-61): 1 pick what goes in the zip (one run, several runs in one zip,
// the imported files or every log), 2 check it, then export. Every zip stays on this
// machine, listed below, until the engineer deletes it.
export default function ExportPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const [params] = useSearchParams();
  const from = params.get("from");
  const back = returnTarget(from, jobId ?? "all");

  const [selection, setSelection] = useState<Selection>(() => initialSelection(jobId));
  const [mask, setMask] = useState(true);
  const [sets, setSets] = useState<LogSet[] | null>(null);
  const [toolNames, setToolNames] = useState<Record<string, string>>({});
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [history, setHistory] = useState<{ exports: Package[]; total_bytes: number } | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const [pkg, setPkg] = useState<Package | null>(null);      // the export being followed
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [lost, setLost] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [saved, setSaved] = useState<{ pkg: Package; name: string } | null>(null);
  const resultRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    listLogSets().then(setSets).catch(() => setSets([]));
    listPlugins().then((l) => setToolNames(Object.fromEntries(l.plugins.map((p) => [p.name, p.display_name || p.name])))).catch(() => undefined);
  }, []);

  useEffect(() => {
    let live = true;
    listExports()
      .then((h) => { if (live) { setHistory(h); setHistoryError(null); } })
      .catch(() => { if (live) setHistoryError("Check the connection to the server, then reload the page."); });
    return () => { live = false; };
  }, [historyKey]);

  // One preview per set of logs picked: a single scope shows its full detail, several runs a row each.
  const scopes = useMemo(() => {
    const one = selectionScope(selection);
    return one ? [one] : selection.kind === "runs" ? selection.runs : [];
  }, [selection]);
  useEffect(() => {
    let live = true;
    setPreviewError(null);
    const missing = scopes.filter((s) => !previews[s]);
    if (!missing.length) return;
    Promise.all(missing.map((s) => getPreview(s).then((p) => [s, p] as const)))
      .then((got) => { if (live) setPreviews((cur) => ({ ...cur, ...Object.fromEntries(got) })); })
      .catch(() => { if (live) setPreviewError("Couldn't load what would go in the zip."); });
    return () => { live = false; };
  }, [scopes]); // eslint-disable-line react-hooks/exhaustive-deps

  // Follow an export while it is prepared: often at first, then gently.
  useEffect(() => {
    if (!isPreparing(pkg)) return;
    const t = window.setTimeout(async () => {
      try {
        const next = await getExport(pkg!.export_id);
        setPkg(next);
        setLost(false);
        if (!isPreparing(next)) { setHistoryKey((k) => k + 1); resultRef.current?.focus(); }
      } catch (e) {
        if (e instanceof ApiError && (e.status === 401 || e.status === 423)) return;
        setLost(true);
        setPkg((p) => (p ? { ...p } : p));    // re-arm the timer
      }
    }, pollDelay(Date.now() - (parseServerTime(pkg?.started_at) ?? Date.now())));
    return () => window.clearTimeout(t);
  }, [pkg]);

  const begin = useCallback(async () => {
    setStarting(true);
    setStartError(null);
    try {
      const p = await startExport(selection, mask);
      setPkg(p);
      setHistoryKey((k) => k + 1);
    } catch (e) {
      setStartError(e instanceof ApiError ? e.message : "Couldn't start the export.");
    } finally {
      setStarting(false);
    }
  }, [selection, mask]);

  const download = useCallback(async (p: Package) => {
    if (!p.file_name) return;
    setDownloading(p.export_id);
    setDownloadError(null);
    try {
      setSaved({ pkg: p, name: await downloadPackage(p.export_id, p.file_name) });
    } catch (e) {
      setDownloadError(e instanceof ApiError ? e.message : "The download did not finish.");
    } finally {
      setDownloading(null);
    }
  }, []);

  const remove = useCallback(async (ids: string[]) => {
    try {
      for (const id of ids) await deleteExport(id);
    } catch (e) {
      setHistoryError(e instanceof ApiError ? e.message : "Couldn't delete the export.");
    }
    if (pkg && ids.includes(pkg.export_id)) setPkg(null);
    setHistoryKey((k) => k + 1);
  }, [pkg]);

  const runs = (sets ?? []).filter((s) => s.job_id !== IMPORTED);
  const exportedRuns = new Set((history?.exports ?? []).flatMap((p) => (p.scope === "runs" ? p.runs : p.scope === "run" ? [p.job_id] : [])));
  const toolName = (t: string) => toolNames[t] ?? t;
  const picked = scopes.map((s) => previews[s]).filter((p): p is Preview => !!p);
  const ready = picked.length === scopes.length && scopes.length > 0;
  const events = picked.reduce((n, p) => n + p.events, 0);
  const flagged = picked.reduce((n, p) => n + p.privacy.flagged_events, 0);
  const unfinished = picked.some((p) => !p.finished);
  // The Prometheus TSDB archive is a binary database: it can't be masked, so a
  // masked zip leaves it out, and an unmasked zip carries it raw (metric labels
  // and all). Make that trade explicit when such an export is in the selection.
  const hasTsdb = picked.some((p) => p.tools.some((t) => t.tool === "prometheus_tsdb"));
  const empty = ready && events === 0 && !picked.some((p) => p.scope === "run");

  // Nothing picked yet is a prompt, not an error: only real problems are shown in red.
  const prompt = scopes.length === 0 ? "Pick one or more runs above." : null;
  const problem = startError ? `Couldn't start the export. ${startError}`
    : unfinished ? "A picked run is still going. Export it once it has finished."
    : empty ? "There's nothing to export here yet."
    : null;
  const summary = selection.kind === "runs"
    ? `${selection.runs.length} run${selection.runs.length === 1 ? "" : "s"}${selection.runs.length > 1 ? " in one zip" : ""}`
    : selection.kind === "imported" ? "Imported files" : "All logs";

  const following = pkg !== null;
  return (
    <div className="export-page">
      {from && <Link to={back.to}>{back.label}</Link>}
      <h1>Export</h1>

      {following ? (
        <Result pkg={pkg} lost={lost} resultRef={resultRef} downloading={downloading === pkg.export_id}
          downloadError={downloadError} onDownload={() => download(pkg)} onAgain={() => setPkg(null)} back={back} showBack={!!from} />
      ) : (
        <>
          <section className="export-step" aria-labelledby="exp-pick">
            <h2 id="exp-pick">What to export</h2>
            <ExportPicker sets={sets} selection={selection} onSelection={setSelection} exported={exportedRuns} toolName={toolName} />
          </section>

          {scopes.length > 0 && (
            <section className="export-step" aria-labelledby="exp-check">
              <h2 id="exp-check">What goes in the zip</h2>
              {previewError && <div className="alert error" role="alert"><strong>{previewError}</strong></div>}
              {!ready && !previewError && <p className="muted" role="status">Checking…</p>}
              {ready && (picked.length === 1 ? <Contents p={picked[0]} toolName={toolName} />
                : <SetContents previews={picked} runs={runs} toolName={toolName} />)}

              {ready && <Privacy previews={picked} />}
              <div className="privacy-switches">
                <label>
                  <input type="checkbox" checked={mask} onChange={(e) => setMask(e.target.checked)} aria-describedby="mask-hint" />
                  Mask flagged PHI / PII (recommended)
                </label>
              </div>
              <p id="mask-hint" className="hint">Replaces flagged values with [REDACTED:kind], as the Logs page does. Packet captures can't be masked, so they're left out.</p>
              {hasTsdb && (
                <div className={mask ? "alert" : "alert warn"} role="status">
                  <strong>Prometheus TSDB archive</strong>
                  {mask
                    ? "This export includes the raw metrics database, a binary file that can't be masked — a masked zip leaves it out. Turn masking off to include it."
                    : "The raw metrics database will be in the zip as-is, metric label sets included. The audit log records this."}
                </div>
              )}
              {!mask && flagged > 0 && (
                <div className="alert warn" role="status">
                  <strong>Masking is off</strong>
                  {flagged.toLocaleString()} flagged log entries will be in the zip as they are. The audit log records this.
                </div>
              )}
            </section>
          )}

          <div className="action-bar">
            <span className={problem ? "text-error action-bar-text" : prompt ? "muted action-bar-text" : "action-bar-text"}
              role={problem ? "alert" : "status"}>
              {problem ?? prompt ?? `${summary} · ${events.toLocaleString()} log entries · ${mask ? "masked" : "not masked"}`}
            </span>
            <button type="button" className="btn" disabled={(!!problem && !startError) || starting || !ready} onClick={begin}>
              {starting ? "Starting…" : "Export zip"}
            </button>
          </div>
        </>
      )}

      <ExportHistory exports={history?.exports ?? null} totalBytes={history?.total_bytes ?? 0} error={historyError}
        onDownload={download} onDelete={remove} busy={downloading} />
      {!following && downloadError && <div className="alert error" role="alert"><strong>The download did not finish</strong>{downloadError} Try again.</div>}

      {saved && <DownloadDialog pkg={saved.pkg} fileName={saved.name} back={back} onClose={() => setSaved(null)} />}
    </div>
  );
}

/** One run, the imported files or all logs: each part of the zip and what's in it. */
function Contents({ p, toolName }: { p: Preview; toolName: (t: string) => string }) {
  const isRun = p.scope === "run";
  const system = p.system ?? [];
  return (
    <ul className="export-list">
      <li><strong>Logs</strong> {p.events ? `${p.events.toLocaleString()} log entries, in the standard format` : "No logs"}</li>
      {isRun && <li>
        <strong>Diagnostic results</strong>{" "}
        {p.tools.length === 0 ? "This run used no diagnostics" : `${p.tools.length} diagnostic${p.tools.length === 1 ? "" : "s"}`}
        {p.tools.length > 0 && (
          <ul className="export-tools">
            {p.tools.map((t) => (
              <li key={t.tool} data-ok={t.ok}>
                <strong>{toolName(t.tool)}</strong> <span className="tool-status">{t.ok ? "Pass" : "Fail"}</span>{" "}
                {t.ok ? <span className="muted">{t.files} file{t.files === 1 ? "" : "s"}</span>
                  : <span>Did not finish during the run: {t.error}. Its error is included.</span>}
              </li>
            ))}
          </ul>
        )}
      </li>}
      {system.length > 0 && <li>
        <strong>Troubleshooting files</strong> {system.filter((x) => x.available).length} of {system.length} can be collected now
        <ul className="export-tools">
          {system.map((x) => (
            <li key={x.key} data-ok={x.available}>
              <strong>{x.label}</strong> <span className="tool-status">{x.available ? "Ready" : "Not available"}</span>{" "}
              {!x.available && <span className="muted">{x.needed}</span>}
            </li>
          ))}
        </ul>
      </li>}
      {p.scope !== "imported" && <li><strong>Findings</strong> {p.findings ? `${p.findings} from the analysis` : "None"}</li>}
      <li><strong>Audit trail and details</strong> {isRun ? "who ran and exported it, and when" : p.scope === "imported" ? "who imported and exported the files, and when" : "every action recorded on this machine"}</li>
    </ul>
  );
}

/** Several runs in one zip: a row per run; each goes in its own folder, in the standard layout. */
function SetContents({ previews, runs, toolName }: { previews: Preview[]; runs: LogSet[]; toolName: (t: string) => string }) {
  return (
    <>
      <p className="muted export-set-note">Each run gets its own folder in the standard layout, with a list of the runs at the top of the zip.</p>
      <div className="table-wrap export-table">
        <table className="log-table">
          <caption className="visually-hidden">Runs in this zip</caption>
          <thead><tr><th scope="col">Run</th><th scope="col">Diagnostics</th><th scope="col">Log entries</th><th scope="col">PHI / PII scan</th></tr></thead>
          <tbody>
            {previews.map((p) => {
              const set = runs.find((r) => r.job_id === p.job_id);
              const ok = p.tools.filter((t) => t.ok).length;
              const scanned = p.privacy.complete_for_packaging || p.events === 0;
              return (
                <tr key={p.job_id}>
                  <td className="nowrap">{set ? runWhen(set) : p.job_id.slice(0, 8)}</td>
                  <td>{p.tools.length ? `${ok} of ${p.tools.length} ran · ${p.tools.map((t) => toolName(t.tool)).join(", ")}` : "None"}</td>
                  <td className="mono">{p.events.toLocaleString()}</td>
                  <td>{scanned ? `${p.privacy.flagged_events.toLocaleString()} flagged` : <span className="text-warn">Incomplete</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** The PHI / PII check for what is picked: complete, or what is missing and where to fix it. */
function Privacy({ previews }: { previews: Preview[] }) {
  const events = previews.reduce((n, p) => n + p.events, 0);
  const flagged = previews.reduce((n, p) => n + p.privacy.flagged_events, 0);
  const incomplete = previews.filter((p) => !(p.privacy.complete_for_packaging || p.events === 0));
  const warnings = [...new Set(incomplete.flatMap((p) => p.warnings))];
  return (
    <div className={`alert ${incomplete.length ? "warn" : "ok"}`} role="status">
      <strong>PHI / PII scan</strong>
      {events === 0 ? "No logs to scan."
        : !incomplete.length ? `All ${events.toLocaleString()} log entries scanned; ${flagged.toLocaleString()} flagged.`
        : "The scan is not complete. You can still export; the zip will say so."}
      {incomplete.length > 0 && (
        <>
          <ul className="export-list">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          {incomplete.length === 1 && <Link to={logsLink(incomplete[0].job_id)}>Scan or check these logs on the Logs page</Link>}
        </>
      )}
    </div>
  );
}

interface ResultProps {
  pkg: Package;
  lost: boolean;
  resultRef: React.RefObject<HTMLHeadingElement | null>;
  downloading: boolean;
  downloadError: string | null;
  onDownload: () => void;
  onAgain: () => void;
  back: { to: string; label: string };
  showBack: boolean;
}

function Result({ pkg, lost, resultRef, downloading, downloadError, onDownload, onAgain, back, showBack }: ResultProps) {
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

      <section className="export-card" aria-labelledby="exp-steps">
        <h3 id="exp-steps">Steps</h3>
        <ExportSteps steps={pkg.steps} live={preparing} />
      </section>

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

      {downloadError && <div className="alert error" role="alert"><strong>The download did not finish</strong>{downloadError} Try again.</div>}
      <div className="export-actions">
        {pkg.downloadable && (
          <button type="button" className="btn" disabled={downloading} onClick={onDownload}>{downloading ? "Downloading…" : "Download zip"}</button>
        )}
        {!preparing && (
          <button type="button" className={pkg.downloadable ? "btn ghost" : "btn"} onClick={onAgain}>
            {pkg.state === "ready" ? "Export something else" : "Try again"}
          </button>
        )}
        {showBack && <Link to={back.to} className="btn ghost">{back.label}</Link>}
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
