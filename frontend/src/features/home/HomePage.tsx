import { Link } from "react-router-dom";
import { isFinished } from "../plugins/api";
import { useJobs } from "../jobs/JobsContext";

export default function HomePage() {
  const { job } = useJobs();
  return (
    <>
      <h1>What would you like to do?</h1>
      <p className="muted">Run diagnostics on the system, or work with logs. You can do either, in any order.</p>

      <div className="card-grid">
        <Link to="/run" className="action-card">
          <h2>Run diagnostics</h2>
          <span className="muted">Netshoot, Wireshark and UMF RTI on the live system. Removed when finished.</span>
        </Link>
        <Link to="/logs" className="action-card">
          <h2>Logs</h2>
          <span className="muted">Choose a time frame, bring logs in, then search and filter them.</span>
        </Link>
      </div>

      {job && (
        <section className="card" style={{ marginTop: 20, maxWidth: 560 }} aria-label="Current run">
          <h3>{isFinished(job.status) ? "Last run" : "Run in progress"}</h3>
          <p style={{ margin: "4px 0 10px" }}>Status: <strong>{job.status.replace("_", " ")}</strong></p>
          <Link to="/run">View run</Link>
        </section>
      )}
    </>
  );
}
