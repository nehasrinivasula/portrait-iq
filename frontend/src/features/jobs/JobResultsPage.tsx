import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiFetch } from "../../api";
import { getJob, isFinished, type Job } from "../plugins/api";
import { runHeadline, stepLabel } from "../plugins/progress";
import { runErrorText } from "../jobs/runStatus";
import { netshootSummary, type PluginResult } from "./netshootResults";
import "./jobResults.css";

const POLL_MS = 2000;

// Netshoot's checks for one run, pass or fail with the reason (SCRUM-91).
export default function JobResultsPage() {
  const { jobId = "" } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [results, setResults] = useState<PluginResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadKey, setLoadKey] = useState(0);

  useEffect(() => {
    if (!jobId) return;
    let active = true;
    let timer: number | undefined;
    async function refresh() {
      try {
        const [nextJob, nextResults] = await Promise.all([
          getJob(encodeURIComponent(jobId)),
          apiFetch<PluginResult[]>(`/api/jobs/${encodeURIComponent(jobId)}/results`),
        ]);
        if (!active) return;
        setJob(nextJob);
        setResults(nextResults);
        setError(null);
        if (!isFinished(nextJob.status)) timer = window.setTimeout(refresh, POLL_MS);
      } catch {
        if (active) setError("Couldn't load this run. Check the link, or try again.");
      }
    }
    void refresh();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId, loadKey]);

  const netshoot = results.find((r) => r.tool === "netshoot");
  const checks = netshoot?.checks ?? [];
  const running = job !== null && !isFinished(job.status);
  const [kind, headline] = job ? runHeadline(job.status) : ["info", ""];

  return (
    <div className="job-results">
      <Link to="/run">Back to Run diagnostics</Link>
      <h1>Network checks</h1>
      <p className="muted">Run <code>{jobId.slice(0, 8)}</code> · Netshoot's checks against the system.</p>

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
      {netshoot?.error && (
        <div className="alert error" role="alert"><strong>Netshoot could not run</strong>{netshoot.error}</div>
      )}

      {checks.length > 0 ? (
        <section aria-labelledby="checks-head">
          <h2 id="checks-head">{netshootSummary(checks)}</h2>
          <ol className="job-results-list">
            {checks.map((check, index) => (
              <li key={`${index}-${check.label}`} className={`job-check ${check.status}`}>
                <span>{check.label}</span>
                <strong>{check.status === "pass" ? "Pass" : "Fail"}</strong>
                {check.status === "fail" && check.reason && <p>{check.reason}</p>}
              </li>
            ))}
          </ol>
        </section>
      ) : job && !error && !netshoot?.error && (
        <p className="muted">{running ? "Netshoot checks are still running…" : "No per-check Netshoot results are available for this run."}</p>
      )}
    </div>
  );
}
