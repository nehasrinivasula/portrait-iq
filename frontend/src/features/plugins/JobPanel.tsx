import { isFinished } from "./api";
import { useJobs } from "../jobs/JobsContext";

const STEP_LABEL: Record<string, string> = {
  deploy_tools: "Starting diagnostics",
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
  failed: ["error", "The run failed"],
  cleanup_failed: ["error", "The run ended, but cleanup did not finish"],
  interrupted: ["warn", "The run was interrupted"],
};

export default function JobPanel() {
  const { job, results, lost, clear } = useJobs();
  if (!job) return null;
  const [kind, headline] = HEADLINE[job.status] ?? ["info", job.status];
  const step = job.current_step ? STEP_LABEL[job.current_step] ?? job.current_step : null;

  return (
    <section className="job" aria-live="polite">
      <div className={`alert ${kind}`} role={kind === "error" ? "alert" : "status"}>
        <strong>{headline}</strong>
        {!isFinished(job.status) && (step ?? "Preparing")}
        {job.status === "cleanup_failed" && "Something may still be running on the system. Check it before leaving the site."}
      </div>
      {lost && <p className="muted" role="status">Lost contact with the server. Still trying; the run is not affected.</p>}

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
      {isFinished(job.status) && <button className="btn ghost" onClick={clear}>Clear</button>}
    </section>
  );
}
