import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { apiFetch, setUnauthorizedHandler, tokenStore } from "../api";

// Real policy is 5 idle minutes (backend SESSION_IDLE_TIMEOUT_SECONDS). The demo
// uses 75s so the 60s warning appears after 15s of inactivity.
const DEMO_IDLE_TIMEOUT = true;
const IDLE_MS = DEMO_IDLE_TIMEOUT ? 75_000 : 5 * 60_000;
const WARN_MS = 60_000;

export const IDLE_LABEL = IDLE_MS % 60_000 === 0 ? `${IDLE_MS / 60_000} minutes` : `${IDLE_MS / 1000} seconds`;

type User = { username: string; name: string };

interface AuthState {
  user: User | null;
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: (reason?: "idle" | "expired") => void;
  notice: string | null;
  /** Seconds until idle sign-out; null until the final minute. */
  idleSecondsLeft: number | null;
}

const Ctx = createContext<AuthState | null>(null);
export const useAuth = () => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [idleSecondsLeft, setIdleSecondsLeft] = useState<number | null>(null);
  const lastActivity = useRef(Date.now());

  const logout = useCallback((reason?: "idle" | "expired") => {
    tokenStore.clear();
    setUser(null);
    setNotice(
      reason === "idle"
        ? `You were signed out after ${IDLE_LABEL} of inactivity.`
        : reason === "expired"
          ? "Your session expired. Please sign in again."
          : null,
    );
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => logout("expired"));
    return () => setUnauthorizedHandler(null);
  }, [logout]);

  // Restore a session after refresh.
  useEffect(() => {
    if (!tokenStore.get()) return setReady(true);
    apiFetch<User>("/api/auth/me")
      .then(setUser)
      .catch(() => tokenStore.clear())
      .finally(() => setReady(true));
  }, []);

  // Sign out after IDLE_MS without input; show a countdown for the final WARN_MS.
  useEffect(() => {
    if (!user) return;
    lastActivity.current = Date.now();
    const events = ["mousemove", "keydown", "click", "touchstart"] as const;
    const touch = () => {
      lastActivity.current = Date.now();
    };
    events.forEach((e) => window.addEventListener(e, touch));
    const tick = window.setInterval(() => {
      const left = IDLE_MS - (Date.now() - lastActivity.current);
      if (left <= 0) logout("idle");
      else setIdleSecondsLeft(left <= WARN_MS ? Math.ceil(left / 1000) : null);
    }, 500);
    return () => {
      window.clearInterval(tick);
      events.forEach((e) => window.removeEventListener(e, touch));
      setIdleSecondsLeft(null);
    };
  }, [user, logout]);

  const login = useCallback(async (username: string, password: string) => {
    const res = await apiFetch<{ access_token: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    tokenStore.set(res.access_token);
    const me = await apiFetch<User>("/api/auth/me");
    setNotice(null);
    setUser(me);
  }, []);

  return <Ctx.Provider value={{ user, ready, login, logout, notice, idleSecondsLeft }}>{children}</Ctx.Provider>;
}
