import type { ExportStep, Package, StepStatus } from "./exportApi";

// Pure helpers behind the Export page, kept apart so they can be tested.

export type AlertKind = "info" | "ok" | "warn" | "error";

/** Where the Export page was opened from, and so where "Back" goes. */
export interface ReturnTarget { to: string; label: string }

export function returnTarget(from: string | null, jobId: string): ReturnTarget {
  if (from === "logs") return { to: logsLink(jobId), label: "Back to Logs" };
  return { to: "/run", label: "Back to Run diagnostics" };
}

/** The Logs page showing the same set: "all" is the unfiltered page. */
export const logsLink = (jobId: string) => (jobId === "all" ? "/logs" : `/logs?job=${encodeURIComponent(jobId)}`);

/** Heading and one-line description of what is being exported. */
export function scopeText(scope: string, jobId: string): { heading: string; about: string } {
  if (scope === "imported") return { heading: "Export imported files", about: "Every log file imported without a run, in one zip file." };
  if (scope === "all") return { heading: "Export all logs", about: "Every log on this machine, from every run and import, in one zip file." };
  return { heading: "Export run", about: `Run ${jobId.slice(0, 8)}: all its logs, diagnostic results and findings in one zip file.` };
}

/** Plain words for a step's status, read by screen readers next to the mark. */
export const STEP_STATUS_TEXT: Record<StepStatus, string> = {
  pending: "not started",
  running: "in progress",
  done: "done",
  warn: "done, with notes",
  failed: "failed",
};

/** The mark shown beside a step. Never the only signal: the status text goes with it. */
export const STEP_MARK: Record<StepStatus, string> = {
  pending: "○",
  running: "●",
  done: "✓",
  warn: "!",
  failed: "✗",
};

export function stepPosition(steps: ExportStep[]): { position: number | null; total: number } {
  const i = steps.findIndex((s) => s.status === "running");
  if (i >= 0) return { position: i + 1, total: steps.length };
  const finished = steps.filter((s) => s.status !== "pending").length;
  return { position: finished > 0 && finished < steps.length ? finished : null, total: steps.length };
}

export function headline(p: Package): [AlertKind, string, string] {
  if (p.state === "preparing") {
    const running = p.steps.find((s) => s.status === "running");
    return ["info", "Preparing the zip", running ? running.label : "Starting…"];
  }
  if (p.state === "interrupted") return ["warn", "The export was interrupted", p.error ?? "Start it again."];
  if (p.state === "failed") return ["error", "The export failed", p.error ?? "Start it again."];
  const failed = p.steps.filter((s) => s.status === "failed").length;
  if (failed) {
    return ["warn", "Export ready, but something is missing",
      `${failed} part${failed === 1 ? "" : "s"} could not be added. Open the steps below to see why.`];
  }
  // Notes on a step (a diagnostic that did not run, a file left out) mean the zip is not
  // "everything", even when nothing failed.
  // The server records every warning as a note on its step too, so notes are counted once, from the steps.
  const notes = issueCount(p.steps) || p.warnings.length;
  if (notes) {
    return ["warn", "Export ready, with notes", `${notes} thing${notes === 1 ? "" : "s"} to check before sharing it. They are listed below.`];
  }
  return ["ok", "Export ready", "Everything is in the zip."];
}

export function issueCount(steps: ExportStep[]): number {
  return steps.reduce((n, s) => n + s.issues.length, 0);
}

export function formatBytes(n: number | null): string {
  if (n === null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) { v /= 1024; u += 1; }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[u]}`;
}

/** Shown where a full hash would not fit; the full value is always one click away. */
export const shortHash = (h: string | null) => (h ? `${h.slice(0, 12)}…` : "—");

export const isPreparing = (p: Package | null) => p?.state === "preparing";
