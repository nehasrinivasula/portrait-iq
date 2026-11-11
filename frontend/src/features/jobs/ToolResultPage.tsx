import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJob, getResults, isFinished, listPlugins, type Job, type ToolResult } from "../plugins/api";
import { stepLabel } from "./runStatus";
import JobResultsPage from "./JobResultsPage";
import "./jobResults.css";

/** Netshoot has its own per-check page; every other tool gets this one. */
export default function ToolRoute() {
  const { tool = "" } = useParams<{ tool: string }>();
  return tool === "netshoot" ? <JobResultsPage /> : <ToolResultPage />;
}

const fileName = (path: string) => path.split(/[\\/]/).pop() || path;

// One tool's outcome for one run: what it did, or why it failed and what to do next.
function ToolResultPage() {
  const { jobId = "", tool = "" } = useParams<{ jobId: string; tool: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [results, setResults] = useState<ToolResult[] | null>(null);
  const [name, setName] = useState(tool);
  const [error, setError] = useState<string | null>(null);
  const [loadKey, setLoadKey] = useState(0);

  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    async function refresh() {
      try {
        const [j, r] = await Promise.all([getJob(encodeURIComponent(jobId)), getResults(encodeURIComponent(jobId))]);
        if (!live) return;
        setJob(j); setResults(r); setError(null);
        if (!isFinished(j.status)) timer = window.setTimeout(refresh, 2000);
      } catch {
        if (live) setError("Couldn't load this run. Check the link, or try again.");
      }
    }
    void refresh();
    listPlugins().then((l) => { const p = l.plugins.find((x) => x.name === tool); if (live && p) setName(p.display_name || tool); }).catch(() => undefined);
    return () => { live = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, [jobId, tool, loadKey]);

  const mine = results?.find((r) => r.tool === tool);
  const others = (results ?? []).filter((r) => r.tool !== tool);
  const running = job !== null && !isFinished(job.status);

  return (
    <div className="job-results">
      <Link to="/run">Back to Run diagnostics</Link>
      <h1>{name}</h1>
      <p className="muted">Run <code>{jobId.slice(0, 8)}</code></p>

      {error && (
        <div className="alert error" role="alert">
          <strong>{error}</strong>
          <button type="button" className="btn ghost" onClick={() => setLoadKey((k) => k + 1)}>Try again</button>
        </div>
      )}
      {!error && !job && <p className="muted" role="status">Loading…</p>}

      {job && !mine && (
        <p className="muted" role="status">
          {running ? `${name} has not finished yet.` : `${name} produced no result in this run. It may not have been selected, or the run stopped before it started.`}
        </p>
      )}

      {mine && "error" in mine && (
        <section aria-labelledby="fail-head">
          <div className="alert error" role="alert">
            <strong id="fail-head">{name} failed{stepLabel(mine.step) ? ` while ${stepLabel(mine.step)!.toLowerCase()}` : ""}</strong>
            {mine.error}
          </div>
          <h2>What to do</h2>
          <ul>
            <li>Fix the cause above, then run {name} again from <Link to="/run">Run diagnostics</Link>.</li>
            {others.some((r) => !("error" in r)) && <li>The other tools in this run finished, and their results are kept.</li>}
            <li>If it keeps failing, the run's audit timeline records every step, and the export includes this error.</li>
          </ul>
        </section>
      )}

      {mine && !("error" in mine) && (
        <section aria-labelledby="ok-head">
          <h2 id="ok-head">Finished</h2>
          <p>{mine.summary || "No summary was reported."}</p>
          {mine.warnings.map((w) => <div key={w} className="alert warn" role="status">{w}</div>)}
          <h2>Files</h2>
          {mine.artifact_paths.length > 0
            ? <ul>{mine.artifact_paths.map((p) => <li key={p}><code>{fileName(p)}</code></li>)}</ul>
            : <p className="muted">This tool saved no files.</p>}
          <p className="muted">Everything it saved is included when you export the run.</p>
        </section>
      )}

      {others.length > 0 && (
        <section aria-labelledby="others-head">
          <h2 id="others-head">Other tools in this run</h2>
          <ul>
            {others.map((r) => (
              <li key={r.tool}>
                <Link to={`/jobs/${encodeURIComponent(jobId)}/results/${encodeURIComponent(r.tool)}`}>{r.tool}</Link>{" "}
                {"error" in r ? "Failed" : "Finished"}
              </li>
            ))}
          </ul>
        </section>
      )}
      {job && isFinished(job.status) && <p><Link to={`/jobs/${encodeURIComponent(jobId)}/export?from=run`}>Export results</Link></p>}
    </div>
  );
}
