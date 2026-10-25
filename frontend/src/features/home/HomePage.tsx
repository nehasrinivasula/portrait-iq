import { Link } from "react-router-dom";

export default function HomePage() {
  return (
    <>
      <h1>What would you like to do?</h1>
      <p className="muted">Export logs from the system, or run diagnostics on it. You can do either, in any order.</p>

      <div className="card-grid">
        <Link to="/logs" className="action-card">
          <h2>Logs</h2>
          <span className="muted">Choose a time frame, then search, filter and export the logs.</span>
        </Link>
        <Link to="/run" className="action-card">
          <h2>Run diagnostics</h2>
          <span className="muted">Netshoot, Wireshark and UMF RTI on the live system. Removed when finished.</span>
        </Link>
      </div>
    </>
  );
}
