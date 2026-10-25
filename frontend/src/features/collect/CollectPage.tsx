import { useState } from "react";
import { useAuth } from "../../auth/AuthContext";
import JobPanel from "../plugins/JobPanel";
import PluginPicker from "../plugins/PluginPicker";
import type { Job } from "../plugins/api";

// MVP landing screen after sign-in: choose diagnostics, run, watch progress.
// Source / time-frame and the log table (SCRUM-45, 52) join here as they land.
export default function CollectPage() {
  const { user, logout } = useAuth();
  const [job, setJob] = useState<Job | null>(null);
  return (
    <>
      <header className="app-header">
        <strong>Portrait IQ</strong>
        <span>
          {user?.name} <button className="btn ghost" onClick={() => logout()}>Sign out</button>
        </span>
      </header>
      <main style={{ padding: "28px 24px" }}>
        <h1>Run diagnostics</h1>
        {job ? <JobPanel key={job.id} initial={job} onNewRun={() => setJob(null)} /> : <PluginPicker onStarted={setJob} />}
      </main>
    </>
  );
}
