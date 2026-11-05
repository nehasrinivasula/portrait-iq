import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { isFinished, type Job } from "./api";
import { useJobs } from "../jobs/JobsContext";
import { formatElapsed, parseServerTime, stepLabel, stepProgress } from "./progress";

const HEADLINE: Record<string, [kind: "info" | "ok" | "error" | "warn", text: string]> = {
  queued: ["info", "Waiting to start"],
  running: ["info", "Running"],
  done: ["ok", "Finished"],
  partial: ["warn", "Finished, but some diagnostics failed"],
  failed: ["error", "The run failed"],
  cleanup_failed: ["error", "The run ended, but cleanup did not finish"],
  interrupted: ["warn", "The run was interrupted"],
};

export default function JobPanel() {
  const { job, results, lost, logPull, clear } = useJobs();
  if (!job) return null;
  const [kind, headline] = HEADLINE[job.status] ?? ["info", job.status];
  const step = job.current_step ? stepLabel(job.current_step) : null;
  const finished = isFinished(job.status);
  const stopped = job.status === "failed" || job.status === "interrupted";

  return (
    <section className="job" aria-live="polite">
      <div className={`alert ${kind}`} role={kind === "error" ? "alert" : "status"}>
        <strong>{headline}</strong>
        {!finished && (step ?? "Preparing")}
        {job.status === "cleanup_failed" && "Something may still be running on the system. Check it before leaving the site."}
        {stopped && step && `Stopped at: ${step}.`}
      </div>
      <RunClock job={job} finished={finished} />
      {!finished && <StepList current={job.current_step} />}
      {lost && <p className="muted" role="status">Lost contact with the server. Still trying; the run is not affected.</p>}

      {logPull && (
        <div className={`alert ${logPull.state === "error" ? "error" : logPull.state === "done" ? "ok" : "info"}`}
          role={logPull.state === "error" ? "alert" : "status"}>
          <strong>System logs</strong>
          {logPull.state === "running" && "Collecting…"}
          {logPull.state === "error" && logPull.message}
          {logPull.state === "done" && (
            <>{logPull.events} events from {logPull.newFiles} new file{logPull.newFiles === 1 ? "" : "s"}.{" "}
              <Link to={`/logs?job=${job.id}`}>View these logs</Link></>
          )}
        </div>
      )}

      {results && results.length > 0 && (
        <ul className="results">
          {results.map((r) => (
            <li key={r.tool} className="card">
              <h3>{r.tool}</h3>
              {"error" in r ? (
                <div className="alert error" role="alert"><strong>Failed</strong>{r.error}</div>
              ) : (
                <>
                  <p>{r.summary}</p>
                  {r.tool === "netshoot" && <Link to={`/jobs/${encodeURIComponent(job.id)}/results`}>View network checks</Link>}
                  {r.warnings.map((w) => <div key={w} className="alert warn" role="status">{w}</div>)}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {finished && <button className="btn ghost" onClick={clear}>Clear</button>}
    </section>
  );
}

/** Ticks every second while the run goes, so a long step never looks frozen.
 * aria-live off: the headline above already announces each step change. */
function RunClock({ job, finished }: { job: Job; finished: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (finished) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [finished]);
  const start = parseServerTime(job.created_at);
  if (start === null) return null;
  const end = finished ? parseServerTime(job.finished_at) : now;
  if (end === null) return null;
  return (
    <p className="muted run-clock" role="timer" aria-live="off">
      {finished ? "Took" : "Running for"} {formatElapsed(end - start)}
    </p>
  );
}

function StepList({ current }: { current: string | null }) {
  const { position, total, steps } = stepProgress(current);
  return (
    <div className="run-steps" aria-live="off">
      {position && <p className="muted">Step {position} of {total}</p>}
      <ol>
        {steps.map((s) => (
          <li key={s.step} data-state={s.state} aria-current={s.state === "current" ? "step" : undefined}>
            <span aria-hidden="true">{s.state === "done" ? "✓" : s.state === "current" ? "●" : "○"}</span>
            {s.label}
            {s.state === "done" && <span className="visually-hidden"> (done)</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}
