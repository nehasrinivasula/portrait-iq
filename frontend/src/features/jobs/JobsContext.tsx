import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { useNotify } from "../../ui/Notifications";
import { getJob, getResults, isFinished, type Job, type ToolResult } from "../plugins/api";
import type { AcquisitionResult } from "../logs/logsApi";
import { activeJobStore, resumeJobId } from "./jobStorage";

const POLL_MS = 2000;

export type LogPull =
  | { state: "running" }
  | { state: "done"; newFiles: number; events: number }
  | { state: "error"; message: string };

interface JobsState {
  /** The system-log pull started with the run, if any. */
  logPull: LogPull | null;
  job: Job | null;
  results: ToolResult[] | null;
  /** True briefly after sign-in while a saved run is being restored. */
  restoring: boolean;
  /** True while polling is failing; the run itself is unaffected (S-2). */
  lost: boolean;
  /** Watch a run; pullLogs, when given, starts the log pull for it right away. */
  track: (job: Job, pullLogs?: () => Promise<AcquisitionResult>) => void;
  clear: () => void;
  /** The Run page's target IP, kept here so it survives leaving the page.
   * Memory only, never browser storage, and forgotten on sign-out. */
  targetIp: string;
  setTargetIp: (ip: string) => void;
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
  const [restoring, setRestoring] = useState(false);
  const [lost, setLost] = useState(false);
  const [logPull, setLogPull] = useState<LogPull | null>(null);
  const [targetIp, setTargetIp] = useState("");
  const announced = useRef<string | null>(null);

  const clear = useCallback(() => { activeJobStore.clear(); setJob(null); setResults(null); setLost(false); setLogPull(null); setRestoring(false); }, []);
  const track = useCallback((j: Job, pullLogs?: () => Promise<AcquisitionResult>) => {
    activeJobStore.set(j.id); setResults(null); setLost(false); setRestoring(false); announced.current = null; setJob(j); setLogPull(null);
    if (!pullLogs) return;
    setLogPull({ state: "running" });
    pullLogs()
      .then((r) => {
        const events = r.files.reduce((n, f) => n + (f.events ?? 0), 0);
        setLogPull({ state: "done", newFiles: r.new_files, events });
        notify({ kind: r.failed ? "warn" : "ok", title: r.failed ? "Some logs couldn't be collected" : "Logs collected", body: `${events} events from ${r.new_files} new file${r.new_files === 1 ? "" : "s"}.` });
      })
      .catch((e) => {
        const message = e instanceof ApiError && e.status !== 0 ? e.message : "Can't reach the server.";
        setLogPull({ state: "error", message });
        notify({ kind: "error", title: "Couldn't collect the logs", body: message });
      });
  }, [notify]);

  useEffect(() => { if (!user) { setJob(null); setResults(null); setLost(false); setLogPull(null); setRestoring(false); setTargetIp(""); } }, [user]);

  // Sign-out removes the bearer token, not the opaque job id. Once the same
  // browser session signs in again, recover the persisted server-side run.
  useEffect(() => {
    let live = true;
    const id = resumeJobId(Boolean(user));
    if (!id) { setRestoring(false); return; }
    setRestoring(true);
    getJob(id)
      .then((j) => {
        if (!live) return;
        if (isFinished(j.status)) announced.current = j.id;
        setJob(j);
      })
      .catch(() => { if (live) activeJobStore.clear(); })
      .finally(() => { if (live) setRestoring(false); });
    return () => { live = false; };
  }, [user]);

  useEffect(() => {
    if (!job || isFinished(job.status)) return;
    const t = window.setTimeout(async () => {
      try {
        const next = await getJob(job.id);
        // Per-tool outcomes appear as each tool finishes, so show them live.
        if (!isFinished(next.status)) await getResults(job.id).then(setResults).catch(() => undefined);
        setJob(next);
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
    else if (job.status === "partial")
      notify({ kind: "warn", title: "Finished, but some diagnostics failed", body: "Open Run diagnostics to see which ones." });
    else if (job.status === "cleanup_failed")
      notify({ kind: "error", title: "Cleanup did not finish", body: "Something may still be running on the system. Check it before leaving the site." });
    else if (job.status === "interrupted") notify({ kind: "warn", title: "The run was interrupted", body: "Partial results are kept." });
    else notify({ kind: "error", title: "The run failed", body: "Open Run diagnostics for details." });
  }, [job, notify]);

  return <Ctx.Provider value={{ job, results, restoring, lost, logPull, track, clear, targetIp, setTargetIp }}>{children}</Ctx.Provider>;
}
