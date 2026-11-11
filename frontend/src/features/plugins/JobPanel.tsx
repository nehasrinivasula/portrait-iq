import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { isFinished, type Job } from "./api";
import { useJobs } from "../jobs/JobsContext";
import { runErrorText, stepLabel as errorStepLabel } from "../jobs/runStatus";
import { formatElapsed, parseServerTime, runHeadline, stepLabel, stepProgress } from "./progress";

export default function JobPanel() {
  const { job, results, lost, logPull, clear } = useJobs();
  if (!job) return null;
  const [kind, headline] = runHeadline(job.status);
  const step = job.current_step ? stepLabel(job.current_step) : null;
  const finished = isFinished(job.status);
  const stopped = job.status === "failed" || job.status === "interrupted";

  return (
    <section className="job" aria-live="polite">
      <div className={`alert ${kind}`} role={kind === "error" ? "alert" : "status"}>
        <strong>{headline}</strong>
        {!finished && (step ?? "Preparing")}
        {/* The failing step, tool and plain reason, so the engineer can tell
            what went wrong and whether to retry (SCRUM-68). */}
        {finished && job.error && <p className="run-error">{runErrorText(job.error)}</p>}
        {job.status === "cleanup_failed" && "Something may still be running on the system. Check it before leaving the site."}
        {stopped && step && `Stopped at: ${step}.`}
      </div>
      <RunClock job={job} finished={finished} />
      {!finished && <StepList current={job.current_step} skipped={job.skipped_steps} />}
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
                <div className="alert error" role="alert">
                  <strong>Failed{errorStepLabel(r.step) ? ` — ${errorStepLabel(r.step)}` : ""}</strong>
                  {r.error}
                </div>
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
      {finished && (
        <div className="job-actions">
          <Link to={`/jobs/${encodeURIComponent(job.id)}/export?from=run`} className="btn">Export results</Link>
          <button type="button" className="btn ghost" onClick={clear}>Clear</button>
        </div>
      )}
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

/** One segment per step, filled as the run advances, so all six stay visible
 * and progress reads as movement along the bar. */
function StepList({ current, skipped = [] }: { current: string | null; skipped?: string[] }) {
  const { position, total, steps } = stepProgress(current, skipped);
  const label = steps.find((s) => s.state === "current")?.label;
  return (
    <div className="run-steps">
      <p className="muted">{skipped.length > 0 && `${skipped.length} of ${total} steps not needed for the selected tools. `}{position ? `Step ${position} of ${total}: ${label}` : "Preparing"}</p>
      <div className="run-steps-bar" role="progressbar" aria-label="Run progress"
        aria-valuemin={0} aria-valuemax={total} aria-valuenow={position ? position - 1 : 0}
        aria-valuetext={position ? `Step ${position} of ${total}: ${label}` : "Preparing"}>
        {steps.map((s) => <span key={s.step} data-state={s.state} title={s.state === "skipped" ? `${s.label}: not needed for this run` : s.label} />)}
      </div>
    </div>
  );
}
