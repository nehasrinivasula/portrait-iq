import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { apiFetch, ApiError, tokenStore } from "../api";

// Matches the backend's 5-minute idle session lock (SESSION_IDLE_TIMEOUT_SECONDS).
const IDLE_MS = 5 * 60 * 1000;

interface AuthState {
  user: { username: string; name: string } | null;
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: (reason?: "idle") => void;
  notice: string | null;
}

const Ctx = createContext<AuthState | null>(null);
export const useAuth = () => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthState["user"]>(null);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const idleTimer = useRef<number | undefined>(undefined);

  const logout = useCallback((reason?: "idle") => {
    tokenStore.clear();
    setUser(null);
    setNotice(reason === "idle" ? "You were signed out after 5 minutes of inactivity." : null);
  }, []);

  // Restore a session after refresh.
  useEffect(() => {
    if (!tokenStore.get()) return setReady(true);
    apiFetch<{ username: string; name: string }>("/api/auth/me")
      .then(setUser)
      .catch(() => tokenStore.clear())
      .finally(() => setReady(true));
  }, []);

  // Sign out after 5 idle minutes; any input resets the timer.
  useEffect(() => {
    if (!user) return;
    const reset = () => {
      window.clearTimeout(idleTimer.current);
      idleTimer.current = window.setTimeout(() => logout("idle"), IDLE_MS);
    };
    const events = ["mousemove", "keydown", "click", "touchstart"] as const;
    events.forEach((e) => window.addEventListener(e, reset));
    reset();
    return () => {
      window.clearTimeout(idleTimer.current);
      events.forEach((e) => window.removeEventListener(e, reset));
    };
  }, [user, logout]);

  const login = useCallback(async (username: string, password: string) => {
    const res = await apiFetch<{ access_token: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    tokenStore.set(res.access_token);
    const me = await apiFetch<{ username: string; name: string }>("/api/auth/me");
    setNotice(null);
    setUser(me);
  }, []);

  return <Ctx.Provider value={{ user, ready, login, logout, notice }}>{children}</Ctx.Provider>;
}

export { ApiError };
