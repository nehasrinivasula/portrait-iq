import { useAuth } from "./AuthContext";
import "./idle-warning.css";

// Appears for the final minute before idle sign-out; any input dismisses it.
export default function IdleWarning() {
  const { idleSecondsLeft } = useAuth();
  if (idleSecondsLeft === null) return null;
  const mm = Math.floor(idleSecondsLeft / 60);
  const ss = String(idleSecondsLeft % 60).padStart(2, "0");
  return (
    <div className="idle-warning" role="alert">
      <span>
        Signing out due to inactivity in <strong>{mm}:{ss}</strong>
      </span>
      <span className="muted">Move the mouse or press a key to stay signed in.</span>
    </div>
  );
}
