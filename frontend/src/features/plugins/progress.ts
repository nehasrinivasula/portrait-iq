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

export type StepState = "done" | "current" | "upcoming";

export interface StepProgress {
  /** 1-based position of the current step, or null before the first step
   * (queued) or for a step this build does not know. */
  position: number | null;
  total: number;
  steps: { step: string; label: string; state: StepState }[];
}

export function stepProgress(current: string | null): StepProgress {
  const i = current ? STEPS.indexOf(current as (typeof STEPS)[number]) : -1;
  return {
    position: i >= 0 ? i + 1 : null,
    total: STEPS.length,
    steps: STEPS.map((step, n) => ({
      step,
      label: stepLabel(step),
      state: i < 0 || n > i ? "upcoming" : n < i ? "done" : "current",
    })),
  };
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
