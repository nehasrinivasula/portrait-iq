import { Link } from "react-router-dom";
import { isFinished } from "./api";
import { useJobs } from "../jobs/JobsContext";

const STEP_LABEL: Record<string, string> = {
  deploy_tools: "Running the diagnostics",
  collect_server_logs: "Collecting logs",
  collect_network_diagnostics: "Collecting network information",
  collect_grafana_health: "Collecting system health",
  cleanup: "Removing diagnostics from the system",
  package: "Preparing results",
};

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
  const step = job.current_step ? STEP_LABEL[job.current_step] ?? job.current_step : null;
  const finished = isFinished(job.status);

  return (
    <section className="job" aria-live="polite">
      <div className={`alert ${kind}`} role={kind === "error" ? "alert" : "status"}>
        <strong>{headline}</strong>
        {!finished && (step ?? "Preparing")}
        {job.status === "cleanup_failed" && "Something may still be running on the system. Check it before leaving the site."}
      </div>
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
