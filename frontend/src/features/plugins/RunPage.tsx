import { useJobs } from "../jobs/JobsContext";
import JobPanel from "./JobPanel";
import PluginPicker from "./PluginPicker";
import { isFinished } from "./api";

// Setup on the left, the run on the right. Starting a run keeps you here.
export default function RunPage() {
  const { job, track } = useJobs();
  const running = job !== null && !isFinished(job.status);
  return (
    <>
      <h1>Run diagnostics</h1>
      <div className="run-layout">
        <PluginPicker onStarted={track} locked={running} />
        <aside className="run-side" aria-label={running ? "Current run" : "Last run"}>
          <h2 style={{ fontSize: 22, margin: 0 }}>{job ? (running ? "Current run" : "Last run") : "Run"}</h2>
          {job ? <JobPanel /> : <p className="muted">Your run appears here: progress while it works, then the results.</p>}
        </aside>
      </div>
    </>
  );
}
