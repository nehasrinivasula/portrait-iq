// The one progress vocabulary for every multi-step job (a run, an export).
// The screen always shows the server's real state: no step is held back for
// readability. Readability comes from each step's duration and from finished
// steps being ticked off in order (see Steps.tsx), not from delay.

export type StepStatus = "pending" | "running" | "done" | "warn" | "failed";

export interface StepIssue { item: string; reason: string }

export interface StepItem {
  id: string;
  label: string;
  status: StepStatus;
  detail?: string | null;
  issues?: StepIssue[];
  /** Epoch ms; null when the server did not say. */
  startedAt?: number | null;
  finishedAt?: number | null;
}

/** Screen-reader words for each status; the drawn mark is never the only signal. */
export const STATUS_TEXT: Record<StepStatus, string> = {
  pending: "not started",
  running: "in progress",
  done: "done",
  warn: "done, with notes",
  failed: "failed",
};

export const isFinished = (s: StepStatus) => s === "done" || s === "warn" || s === "failed";

/** 1-based position of the running step (or of the last finished one while between steps); null before and after. */
export function stepPosition(steps: StepItem[]): { position: number | null; total: number } {
  const i = steps.findIndex((s) => s.status === "running");
  if (i >= 0) return { position: i + 1, total: steps.length };
  const finished = steps.filter((s) => s.status !== "pending").length;
  return { position: finished > 0 && finished < steps.length ? finished : null, total: steps.length };
}

/** "0.4 s", "12 s", "1 min 05 s". Under 10 s keeps one decimal, since fast steps are the ones people squint at. */
export function formatDuration(ms: number): string {
  const s = Math.max(0, ms) / 1000;
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${String(Math.round(s % 60)).padStart(2, "0")} s`;
}

/** A step's duration, once it has both ends. */
export function stepDuration(s: StepItem): string | null {
  return s.startedAt != null && s.finishedAt != null ? formatDuration(s.finishedAt - s.startedAt) : null;
}

/** Wall time from the first start to the last finish, for the "6 steps · 4.1 s" summary. */
export function totalDuration(steps: StepItem[]): string | null {
  const starts = steps.map((s) => s.startedAt).filter((t): t is number => t != null);
  const ends = steps.map((s) => s.finishedAt).filter((t): t is number => t != null);
  return starts.length && ends.length ? formatDuration(Math.max(...ends) - Math.min(...starts)) : null;
}

/** Ids that became finished since the last render, in list order: these get ticked off one after another. */
export function newlyFinished(prev: Map<string, StepStatus> | null, steps: StepItem[]): string[] {
  if (!prev) return [];      // first paint: show history as it is, no replay
  return steps.filter((s) => isFinished(s.status) && !isFinished(prev.get(s.id) ?? "pending")).map((s) => s.id);
}

/** How often to ask the server: quickly at first (most steps are short), then gently. */
export const pollDelay = (elapsedMs: number) => (elapsedMs < 10_000 ? 400 : 1500);
