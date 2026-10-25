import { useState, type SubmitEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import * as Label from "@radix-ui/react-label";
import { ApiError } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import SystemInfoPanel from "../system/SystemInfoPanel";
import "./login.css";

type Reason = { kind: "error" | "warn"; title: string; body: string };

// Maps backend/network failures to a specific, actionable reason (SCRUM-32).
export function reasonFor(err: unknown): Reason {
  if (err instanceof ApiError) {
    const code = err.status === 0 ? "network" : err.code;
    switch (code) {
      case "network":
        return { kind: "error", title: "Can't reach the server", body: "Check your network connection and that the Portrait IQ service is running, then try again." };
      case "invalid_credentials":
        return { kind: "error", title: "Incorrect username or password", body: "Check your credentials and try again." };
      case "not_configured":
        return { kind: "warn", title: "Sign-in isn't set up on this server", body: "Demo credentials haven't been configured. Ask your administrator to set them in the backend .env file." };
    }
    if (err.status >= 500)
      return { kind: "error", title: "Server error", body: "Something went wrong on the server. Try again in a moment." };
  }
  return { kind: "error", title: "Sign-in failed", body: "An unexpected error occurred. Try again." };
}

export default function LoginPage() {
  const { user, login, notice } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [touched, setTouched] = useState({ username: false, password: false });
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState<Reason | null>(null);
  const [capsOn, setCapsOn] = useState(false);

  if (user) return <Navigate to="/collect" replace />;

  const missing = {
    username: !username.trim() && "Enter your username.",
    password: !password && "Enter your password.",
  };

  async function onSubmit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault();
    setTouched({ username: true, password: true });
    if (missing.username || missing.password) return;
    setBusy(true);
    setReason(null);
    try {
      await login(username.trim(), password);
      navigate("/collect", { replace: true });
    } catch (err) {
      setReason(reasonFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="card login-card">
        <div className="login-main">
          <div className="login-brand">Portrait IQ</div>
          <h1>Sign in</h1>
          <p className="muted" style={{ margin: 0 }}>Use your field engineer credentials.</p>

          <div aria-live="polite">
            {notice && !reason && (
              <div className="alert info" role="status"><strong>Signed out</strong>{notice}</div>
            )}
            {reason && (
              <div className={`alert ${reason.kind}`} role="alert">
                <strong>{reason.title}</strong>
                {reason.body}
              </div>
            )}
          </div>

          <form onSubmit={onSubmit} noValidate>
            <div className="field">
              <Label.Root htmlFor="username">Username</Label.Root>
              <input
                id="username"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                onBlur={() => setTouched((t) => ({ ...t, username: true }))}
                aria-invalid={touched.username && !!missing.username}
                aria-describedby={touched.username && missing.username ? "username-err" : undefined}
              />
              {touched.username && missing.username && (
                <div id="username-err" className="error-text">{missing.username}</div>
              )}
            </div>

            <div className="field">
              <Label.Root htmlFor="password">Password</Label.Root>
              <div className="pw-row">
                <input
                  id="password"
                  type={showPw ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyUp={(e) => setCapsOn(e.getModifierState("CapsLock"))}
                  onKeyDown={(e) => setCapsOn(e.getModifierState("CapsLock"))}
                  onBlur={() => {
                    setCapsOn(false);
                    setTouched((t) => ({ ...t, password: true }));
                  }}
                  aria-invalid={touched.password && !!missing.password}
                  aria-describedby={touched.password && missing.password ? "password-err" : undefined}
                />
                <button type="button" className="btn ghost" onClick={() => setShowPw((s) => !s)} aria-pressed={showPw}>
                  {showPw ? "Hide" : "Show"}
                </button>
              </div>
              {capsOn && (
                <div className="caps-warning" role="status">⚠ Caps Lock is on</div>
              )}
              {touched.password && missing.password && (
                <div id="password-err" className="error-text">{missing.password}</div>
              )}
            </div>

            <button className="btn" type="submit" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>
        <SystemInfoPanel />
      </div>
    </div>
  );
}
