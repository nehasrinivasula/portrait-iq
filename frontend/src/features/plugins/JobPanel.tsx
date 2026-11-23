import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { isFinished, listPlugins, type Job, type ToolResult } from "./api";
import { useJobs } from "../jobs/JobsContext";
import { runErrorText, stepLabel as errorStepLabel } from "../jobs/runStatus";
import { formatElapsed, parseServerTime, runHeadline, stepLabel, stepProgress } from "./progress";
import Steps from "../../ui/Steps";
import type { StepItem } from "../../ui/stepModel";

/** The run's steps in the shared vocabulary, with the server's times. The step a
 * stopped run was on is shown as failed. Real state only: nothing is held back. */
function runSteps(job: Job): StepItem[] {
  const stopped = job.status === "failed" || job.status === "interrupted";
  const complete = isFinished(job.status) && !stopped;
  const times = job.step_times ?? {};
  return stepProgress(job.current_step, job.skipped_steps, complete).steps.map(({ step, label, state }) => ({
    id: step, label,
    status: state === "done" ? "done" : state === "current" ? (stopped ? "failed" : "running") : "pending",
    startedAt: parseServerTime(times[step]?.[0]),
    finishedAt: parseServerTime(times[step]?.[1]),
  }));
}

export default function JobPanel() {
  const { job, results, lost, logPull, clear } = useJobs();
  const names = useToolNames();
  if (!job) return null;
  const finished = isFinished(job.status);
  const [kind, headline] = runHeadline(job.status);
  const step = job.current_step ? stepLabel(job.current_step) : null;
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
      <Steps steps={runSteps(job)} live={!finished} collapsible
        under={{ deploy_tools: <ToolRows job={job} results={results} stepIsCurrent={job.current_step === "deploy_tools"} /> }} />
      {lost && <p className="muted" role="status">Lost contact with the server. Still trying; the run is not affected.</p>}

      {logPull && (
        <div className={`alert ${logPull.state === "error" ? "error" : logPull.state === "done" ? (logPull.skipped ? "warn" : "ok") : "info"}`}
          role={logPull.state === "error" ? "alert" : "status"}>
          <strong>System logs</strong>
          {logPull.state === "running" && "Collecting…"}
          {logPull.state === "error" && logPull.message}
          {logPull.state === "done" && (
            <>{logPull.events} events from {logPull.newFiles} new file{logPull.newFiles === 1 ? "" : "s"}. {logPull.skipped > 0 && <>{logPull.skipped} malformed log lines skipped. </>}
              <Link to={`/logs?job=${job.id}`}>View these logs</Link></>
          )}
        </div>
      )}

      {finished && results && results.length > 0 && (
        <ul className="results">
          {results.map((r) => (
            <li key={r.tool} className="card">
              <h3>{names[r.tool] ?? r.tool}</h3>
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
