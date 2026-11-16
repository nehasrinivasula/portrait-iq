import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api";
import { useJobs } from "../jobs/JobsContext";
import { checkConnection, detectConnection, type ConnectionSetupResult, type ConnectivityResult } from "./connectionApi";
import "./connect.css";

type FieldErrors = { ip?: string; username?: string; password?: string };

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "Could not contact the Portrait IQ server. Check your connection and try again.";
}

function values(values: string[]) {
  return values.length ? values.join(", ") : "Not detected";
}

export default function ConnectionSetupPage() {
  const { setTargetIp, setConnectionSettings } = useJobs();
  const [ipAddress, setIpAddress] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [preflight, setPreflight] = useState<ConnectivityResult | null>(null);
  const [result, setResult] = useState<ConnectionSetupResult | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const next: FieldErrors = {};
    const target = ipAddress.trim();
    if (!target) next.ip = "Enter the Portrait Mobile system IP address.";
    if (!username.trim()) next.username = "Enter your username.";
    if (!password) next.password = "Enter your password.";
    setErrors(next);
    setRequestError(null);
    setResult(null);
    if (Object.keys(next).length) return;

    setBusy(true);
    try {
      const checked = await checkConnection(target);
      setPreflight(checked);
      if (!checked.reachable) return;
      const detected = await detectConnection({ ip_address: target, username: username.trim(), password });
      setResult(detected);
      setTargetIp(detected.ip_address);
      setConnectionSettings(detected.settings);
    } catch (error) {
      setRequestError(errorMessage(error));
    } finally {
      // Never retain a connection credential after this one request attempt.
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <section className="connect-page">
      <h1>Connect to the Portrait Mobile system</h1>
      <p className="muted">Enter the system address once. Portrait IQ checks reachability, then prepares safe starting settings for a diagnostic run.</p>

      <div className="connect-layout">
        <form className="card connect-form" onSubmit={submit} noValidate>
          <h2>Connection information</h2>
          <p className="hint">Credentials are used only for this request. They are not saved, displayed, or written to the audit log.</p>
          <div className="field">
            <label htmlFor="system-ip">Portrait Mobile system IP address</label>
            <input id="system-ip" value={ipAddress} onChange={(e) => setIpAddress(e.target.value)} placeholder="192.168.0.3" inputMode="decimal" autoComplete="off" aria-invalid={Boolean(errors.ip)} aria-describedby={errors.ip ? "system-ip-error" : undefined} />
            {errors.ip && <div id="system-ip-error" className="error-text">{errors.ip}</div>}
          </div>
          <div className="field">
            <label htmlFor="connection-username">Username</label>
            <input id="connection-username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" aria-invalid={Boolean(errors.username)} aria-describedby={errors.username ? "connection-username-error" : undefined} />
            {errors.username && <div id="connection-username-error" className="error-text">{errors.username}</div>}
          </div>
          <div className="field">
            <label htmlFor="connection-password">Password</label>
            <input id="connection-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" aria-invalid={Boolean(errors.password)} aria-describedby={errors.password ? "connection-password-error" : undefined} />
            {errors.password && <div id="connection-password-error" className="error-text">{errors.password}</div>}
          </div>
          <button className="btn" type="submit" disabled={busy}>{busy ? "Checking connection…" : "Check connection and detect settings"}</button>
          {requestError && <div className="alert error" role="alert"><strong>Couldn’t set up this connection</strong>{requestError}</div>}
        </form>

        <div className="connect-output" aria-live="polite">
          <h2>Detected settings</h2>
          {!preflight && <div className="card muted">Run the connection check to see reachability and prefilled settings.</div>}
          {preflight && <div className={`alert ${preflight.reachable ? "ok" : "error"}`}><strong>{preflight.reachable ? "System reachable" : "System not reachable"}</strong>{preflight.summary}<ul className="connect-ports">{preflight.results.map((item) => <li key={item.port}>Port {item.port}: {item.message}</li>)}</ul></div>}
          {result && <div className="card connect-settings">
            <h3>Starting configuration</h3>
            <dl>
              <div><dt>Tools namespace</dt><dd>{result.settings.namespace}<span>Configured for Portrait IQ</span></dd></div>
              <div><dt>Node IPs</dt><dd>{values(result.settings.node_ips)}<span>Resolved from the target address</span></dd></div>
              <div><dt>Interfaces</dt><dd>{values(result.settings.interfaces)}<span>Interfaces on the Portrait IQ tool host</span></dd></div>
              <div><dt>DDS domain IDs</dt><dd>{values(result.settings.dds_domain_ids.map(String))}<span>Requires approved RTI discovery; not guessed</span></dd></div>
            </dl>
            <ul className="connect-notes">{result.notes.map((note) => <li key={note}>{note}</li>)}</ul>
            <Link className="btn" to="/run">Use this IP for a diagnostic run</Link>
          </div>}
        </div>
      </div>
    </section>
  );
}
