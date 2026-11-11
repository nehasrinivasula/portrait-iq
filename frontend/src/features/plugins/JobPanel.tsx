import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { isFinished, listPlugins, type Job, type ToolResult } from "./api";
import { useJobs } from "../jobs/JobsContext";
import { runErrorText, stepLabel as errorStepLabel } from "../jobs/runStatus";
import { formatElapsed, nextPaced, parseServerTime, runHeadline, STEPS, stepIndex, stepLabel, stepProgress } from "./progress";

/** Least time each step stays on screen, so a person can follow a run whose
 * steps finish faster than the page polls. */
const STEP_MIN_MS = 900;

/** The step index to show: trails the run's real step, one step per STEP_MIN_MS.
 * A failed or interrupted run, or reduced-motion, shows the truth at once. */
function usePacedStep(job: Job | null): number {
  const calm = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const reached = job ? job.status === "failed" || job.status === "interrupted" : false;
  const complete = job ? isFinished(job.status) && !reached : false;
  const target = job ? stepIndex(job.current_step, complete) : -1;
  const instant = calm || reached;
  const skipped = job?.skipped_steps ?? [];
  const [shown, setShown] = useState(target);
  const id = job?.id;
  useEffect(() => { setShown(target); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (instant) { if (shown !== target) setShown(target); return; }
    if (shown >= target) { if (shown > target) setShown(target); return; }
    const t = window.setTimeout(() => setShown(nextPaced(shown, target, skipped)), STEP_MIN_MS);
    return () => window.clearTimeout(t);
  }, [shown, target, instant, skipped.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  return instant ? target : shown;
}

export default function JobPanel() {
  const { job, results, lost, logPull, clear } = useJobs();
  const shown = usePacedStep(job);
  if (!job) return null;
  const realFinished = isFinished(job.status);
  const complete = realFinished && job.status !== "failed" && job.status !== "interrupted";
  // A run can be done before the bar has caught up: keep it "running" until it has.
  const settling = complete && shown < STEPS.length;
  const finished = realFinished && !settling;
  const [kind, headline] = runHeadline(settling ? "running" : job.status);
  const shownStep = shown >= 0 && shown < STEPS.length ? STEPS[shown] : null;
  const step = (settling || !realFinished ? shownStep : job.current_step) ? stepLabel((settling || !realFinished ? shownStep : job.current_step)!) : null;
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
      {!finished && <StepList current={shownStep} skipped={job.skipped_steps}
        under={{ deploy_tools: <ToolRows job={job} results={results} stepIsCurrent={shownStep === "deploy_tools"} /> }} />}
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

      {finished && results && results.length > 0 && (
        <ul className="results">
          {results.map((r) => (
            <li key={r.tool} className="card">
              <h3>{r.tool}</h3>
              {"error" in r ? (
                <div className="alert error" role="alert">
                  <strong>Failed{errorStepLabel(r.step) ? ` — ${errorStepLabel(r.step)}` : ""}</strong>
                  {r.error}
                  {" "}<Link to={toolDetailsPath(job.id, r.tool)}>View details</Link>
                </div>
              ) : (
                <>
                  <p>{r.summary}</p>
                  <Link to={toolDetailsPath(job.id, r.tool)}>{r.tool === "netshoot" ? "View network checks" : "View details"}</Link>
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

export const toolDetailsPath = (jobId: string, tool: string) =>
  `/jobs/${encodeURIComponent(jobId)}/results/${encodeURIComponent(tool)}`;

/** Display names for the tools, loaded once; the tool id is the fallback. */
function useToolNames(): Record<string, string> {
  const [names, setNames] = useState<Record<string, string>>({});
  useEffect(() => {
    let live = true;
    listPlugins().then((l) => { if (live) setNames(Object.fromEntries(l.plugins.map((p) => [p.name, p.display_name || p.name]))); }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  return names;
}

/** One row per selected tool under "Running the diagnostics": waiting, running,
 * then Pass or Fail. Status only: the reason and the details open from the results once the run ends. */
function ToolRows({ job, results, stepIsCurrent }: { job: Job; results: ToolResult[] | null; stepIsCurrent: boolean }) {
  const names = useToolNames();
  const tools = job.tools ?? [];
  if (tools.length === 0) return null;
  const byTool = new Map((results ?? []).map((r) => [r.tool, r]));
  const runningTool = stepIsCurrent ? tools.find((t) => !byTool.has(t)) : undefined;
  return (
    <ul className="tool-rows">
      {tools.map((t) => {
        const r = byTool.get(t);
        const failed = r !== undefined && "error" in r;
        const state = r ? (failed ? "Fail" : "Pass") : t === runningTool ? "Running" : "Waiting";
        return (
          <li key={t} data-state={state.toLowerCase()}>
            <span>{names[t] ?? t}</span>
            <strong className="tool-state">{state}</strong>
          </li>
        );
      })}
    </ul>
  );
}

/** All six steps, top to bottom, each with a dot: filled once done, ringed
 * while current, hollow while waiting. Only the steps this run uses. */
function StepList({ current, skipped = [], under = {} }: { current: string | null; skipped?: string[]; under?: Record<string, ReactNode> }) {
  const { position, total, steps } = stepProgress(current, skipped);
  return (
    <div className="run-steps" aria-live="off">
      {position && <p className="muted">Step {position} of {total}</p>}
      <ol>
        {steps.map((s) => (
          <li key={s.step} data-state={s.state} aria-current={s.state === "current" ? "step" : undefined}>
            <span className="step-dot" aria-hidden="true" />
            {s.label}
            {s.state === "done" && <span className="visually-hidden"> (done)</span>}
            {under[s.step]}
          </li>
        ))}
      </ol>
    </div>
  );
}
