// Where a run is among its steps, and how long it has taken (SCRUM-41).

/** The orchestrator's steps, in run order. Mirrors STEPS in
 * backend/app/orchestrator/jobs.py; keep the two in step. */
export const STEPS = [
  "deploy_tools",
  "collect_server_logs",
  "collect_network_diagnostics",
  "collect_grafana_health",
  "cleanup",
  "package",
] as const;

export const STEP_LABEL: Record<string, string> = {
  deploy_tools: "Running the diagnostics",
  collect_server_logs: "Collecting logs",
  collect_network_diagnostics: "Collecting network information",
  collect_grafana_health: "Collecting system health",
  cleanup: "Removing diagnostics from the system",
  package: "Preparing results",
};

export const stepLabel = (step: string) => STEP_LABEL[step] ?? step;

export type StepState = "done" | "current" | "upcoming" | "skipped";

export interface StepProgress {
  /** 1-based position of the current step, or null before the first step
   * (queued) or for a step this build does not know. */
  position: number | null;
  total: number;
  steps: { step: string; label: string; state: StepState }[];
}

export function stepProgress(current: string | null, skipped: readonly string[] = [], complete = false): StepProgress {
  const i = complete ? STEPS.length : current ? STEPS.indexOf(current as (typeof STEPS)[number]) : -1;
  return {
    position: i >= 0 && i < STEPS.length ? i + 1 : null,
    total: STEPS.length,
    steps: STEPS.map((step, n) => ({
      step,
      label: stepLabel(step),
      state: skipped.includes(step) ? "skipped" : i < 0 || n > i ? "upcoming" : n < i ? "done" : "current",
    })),
  };
}

/** Index the run has really reached: -1 before the first step, STEPS.length
 * once every step is done. */
export function stepIndex(current: string | null, complete: boolean): number {
  return complete ? STEPS.length : current ? STEPS.indexOf(current as (typeof STEPS)[number]) : -1;
}

/** The next index to show on the way from `shown` to `target`, one step at a
 * time and passing over steps the run skips, so each step gets its moment on
 * screen even when the server finished it between two polls. */
export function nextPaced(shown: number, target: number, skipped: readonly string[] = []): number {
  for (let n = shown + 1; n < target; n++) {
    if (n >= STEPS.length || !skipped.includes(STEPS[n])) return n;
  }
  return Math.max(shown, target);
}

/** Parses an API timestamp. Times without an offset are UTC (the backend
 * stores naive UTC), not the browser's local time. */
export function parseServerTime(ts: string | null | undefined): number | null {
  if (!ts) return null;
  const t = Date.parse(/(Z|[+-]\d\d:?\d\d)$/i.test(ts) ? ts : `${ts}Z`);
  return Number.isNaN(t) ? null : t;
}

/** "45 s", "3 min 05 s", "1 h 02 min". Negative (clock skew) counts as 0. */
export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  if (h > 0) return `${h} h ${pad(m)} min`;
  if (m > 0) return `${m} min ${pad(s % 60)} s`;
  return `${s} s`;
}

export type AlertKind = "info" | "ok" | "warn" | "error";

/** A run's status in plain words, with the alert style that goes with it.
 * Shared by the run panel and the network checks page. */
const RUN_HEADLINE: Record<string, [AlertKind, string]> = {
  queued: ["info", "Waiting to start"],
  running: ["info", "Running"],
  done: ["ok", "Finished"],
  partial: ["warn", "Finished, but some diagnostics failed"],
  failed: ["error", "The run failed"],
  cleanup_failed: ["error", "The run ended, but cleanup did not finish"],
  interrupted: ["warn", "The run was interrupted"],
};

export const runHeadline = (status: string): [AlertKind, string] => RUN_HEADLINE[status] ?? ["info", status];
