import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJob, getResults, isFinished, listPlugins, type Job, type ToolResult } from "../plugins/api";
import { runHeadline } from "../plugins/progress";
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

  const [kind, headline] = job ? runHeadline(job.status) : ["info", ""];
  const failed = mine !== undefined && "error" in mine;
  const rows: { label: string; word: string; cls: "pass" | "fail" | "warn"; note?: string }[] = !mine ? [] : "error" in mine
    ? [{ label: name, word: "Fail", cls: "fail", note: mine.error }]
    : [
        { label: name, word: "Pass", cls: "pass" as const },
        ...mine.warnings.map((w) => ({ label: "Warning", word: "Warning", cls: "warn" as const, note: w })),
        ...mine.artifact_paths.map((p) => ({ label: fileName(p), word: "Saved", cls: "pass" as const })),
      ];

  return (
    <div className="job-results">
      <Link to="/run">Back to Run diagnostics</Link>
      <h1>{name}</h1>
      <p className="muted">Run <code>{jobId.slice(0, 8)}</code> · {name}'s result for this run.</p>

      {error && (
        <div className="alert error" role="alert">
          <strong>{error}</strong>
          <button type="button" className="btn ghost" onClick={() => setLoadKey((k) => k + 1)}>Try again</button>
        </div>
      )}
      {!error && !job && <p className="muted" role="status">Loading…</p>}
      {job && (
        <div className={`alert ${kind}`} role={kind === "error" ? "alert" : "status"}>
          <strong>{headline}</strong>
          {running && job.current_step && stepLabel(job.current_step)}
        </div>
      )}
      {mine && "error" in mine && (
        <div className="alert error" role="alert">
          <strong>{name} could not run{stepLabel(mine.step) ? ` while ${stepLabel(mine.step)!.toLowerCase()}` : ""}</strong>
          {mine.error}
        </div>
      )}

      {mine ? (
        <section aria-labelledby="result-head">
          <h2 id="result-head">{failed ? `${name} failed` : (mine as { summary: string }).summary || `${name} finished`}</h2>
          <ol className="job-results-list">
            {rows.map((r, i) => (
              <li key={`${i}-${r.label}`} className={`job-check ${r.cls}`}>
                <span>{r.label}</span>
                <strong>{r.word}</strong>
                {r.note && <p>{r.note}</p>}
              </li>
            ))}
          </ol>
          {failed && (
            <p className="muted">
              Fix the cause above, then run {name} again from <Link to="/run">Run diagnostics</Link>.
              {others.some((r) => !("error" in r)) && " The other tools in this run finished, and their results are kept."}
            </p>
          )}
        </section>
      ) : job && !error && (
        <p className="muted">{running ? `${name} has not finished yet…` : `${name} produced no result in this run. It may not have been selected, or the run stopped before it started.`}</p>
      )}

      {others.length > 0 && (
        <section aria-labelledby="others-head">
          <h2 id="others-head">Other tools in this run</h2>
          <ol className="job-results-list">
            {others.map((r) => (
              <li key={r.tool} className={`job-check ${"error" in r ? "fail" : "pass"}`}>
                <Link to={`/jobs/${encodeURIComponent(jobId)}/results/${encodeURIComponent(r.tool)}`}>{r.tool}</Link>
                <strong>{"error" in r ? "Fail" : "Pass"}</strong>
              </li>
            ))}
          </ol>
        </section>
      )}
      {job && isFinished(job.status) && <p><Link to={`/jobs/${encodeURIComponent(jobId)}/export?from=run`}>Export results</Link></p>}
    </div>
  );
}
