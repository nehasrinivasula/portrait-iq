import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { useNotify } from "../../ui/Notifications";
import { getJob, getResults, isFinished, type Job, type ToolResult } from "../plugins/api";

const POLL_MS = 2000;
const KEY = "piq.job";
const store = {
  get: () => { try { return sessionStorage.getItem(KEY); } catch { return null; } },
  set: (id: string) => { try { sessionStorage.setItem(KEY, id); } catch { /* private mode: lose the run on reload */ } },
  clear: () => { try { sessionStorage.removeItem(KEY); } catch { /* ignore */ } },
};

interface JobsState {
  job: Job | null;
  results: ToolResult[] | null;
  /** True while polling is failing; the run itself is unaffected (S-2). */
  lost: boolean;
  track: (job: Job) => void;
  clear: () => void;
}

const Ctx = createContext<JobsState | null>(null);
export const useJobs = () => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useJobs outside JobsProvider");
  return v;
};

// Lives above the router so a run keeps being watched while the engineer looks
// at logs, and the finish notice appears on whichever page they are on.
export function JobsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const notify = useNotify();
  const [job, setJob] = useState<Job | null>(null);
  const [results, setResults] = useState<ToolResult[] | null>(null);
  const [lost, setLost] = useState(false);
  const announced = useRef<string | null>(null);

  const clear = useCallback(() => { store.clear(); setJob(null); setResults(null); setLost(false); }, []);
  const track = useCallback((j: Job) => { store.set(j.id); setResults(null); setLost(false); announced.current = null; setJob(j); }, []);

  useEffect(() => { if (!user) { setJob(null); setResults(null); setLost(false); } }, [user]);

  // A page reload must not lose a run that is still going: pick it back up.
  useEffect(() => {
    const id = user ? store.get() : null;
    if (!id) return;
    getJob(id)
      .then((j) => { if (isFinished(j.status)) announced.current = j.id; setJob(j); })
      .catch(() => store.clear());
  }, [user]);

  useEffect(() => {
    if (!job || isFinished(job.status)) return;
    const t = window.setTimeout(async () => {
      try {
        setJob(await getJob(job.id));
        setLost(false);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return;
        setLost(true);
        setJob((j) => (j ? { ...j } : j)); // re-arm the timer
      }
    }, POLL_MS);
    return () => window.clearTimeout(t);
  }, [job]);

  useEffect(() => {
    if (!job || !isFinished(job.status)) return;
    getResults(job.id).then(setResults).catch(() => setResults([]));
    if (announced.current === job.id) return;
    announced.current = job.id;
    if (job.status === "done") notify({ kind: "ok", title: "Diagnostics finished", body: "Results are ready on the Run diagnostics page." });
    else if (job.status === "cleanup_failed")
      notify({ kind: "error", title: "Cleanup did not finish", body: "Something may still be running on the system. Check it before leaving the site." });
    else if (job.status === "interrupted") notify({ kind: "warn", title: "The run was interrupted", body: "Partial results are kept." });
    else notify({ kind: "error", title: "The run failed", body: "Open Run diagnostics for details." });
  }, [job, notify]);

  return <Ctx.Provider value={{ job, results, lost, track, clear }}>{children}</Ctx.Provider>;
}
