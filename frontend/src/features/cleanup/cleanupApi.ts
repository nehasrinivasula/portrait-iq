import { apiFetch } from "../../api";

export type CleanupState = "clean" | "preserved" | "busy" | "pending" | "unchecked";
/** Why the prompt is open; recorded in the audit log. */
export type CleanupContext = "logout" | "restart" | "manual";

export interface CleanupItem {
  id: string;
  kind: "pod" | "files";
  label: string;
  detail: string;
  kept: boolean;
  /** Belongs to a run still in progress: listed, but cannot be removed. */
  active: boolean;
}
export interface CleanupGroup { job_id: string; status: string | null; items: CleanupItem[] }
export interface CleanupStatus {
  state: CleanupState;
  pods_error: string | null;
  interrupted_runs: number;
  pending_count: number;
  groups: CleanupGroup[];
}
export interface CleanupOutcome { id: string; result: "removed" | "kept" | "skipped" | "failed"; reason: string | null }
export interface CleanupResult { outcomes: CleanupOutcome[]; status: CleanupStatus }

export const getCleanup = () => apiFetch<CleanupStatus>("/api/cleanup");

export const chooseCleanup = (action: "remove" | "keep", context: CleanupContext, items: string[]) =>
  apiFetch<CleanupResult>("/api/cleanup", { method: "POST", body: JSON.stringify({ action, context, items }) });

/** Ends the session on the server so the token stops working. Never throws: signing out must not fail. */
export const endServerSession = (leftoversUndecided = 0) =>
  apiFetch(`/api/auth/logout${leftoversUndecided > 0 ? `?leftovers=${Math.min(leftoversUndecided, 100000)}` : ""}`,
    { method: "POST" }).catch(() => undefined);

// Pure helpers ------------------------------------------------------------------

export const allItems = (s: CleanupStatus): CleanupItem[] => s.groups.flatMap((g) => g.items);
const removable = (s: CleanupStatus) => allItems(s).filter((i) => !i.active);

/** Tool pods ("deployments") that can be removed now. */
export const podIds = (s: CleanupStatus): string[] => removable(s).filter((i) => i.kind === "pod").map((i) => i.id);
/** Everything still waiting for a choice. */
export const pendingIds = (s: CleanupStatus): string[] => removable(s).filter((i) => !i.kept).map((i) => i.id);
/** How many pods and file sets a set of ids covers, for the "are you sure" text. */
export function countSelection(s: CleanupStatus, ids: string[]): { pods: number; files: number } {
  const chosen = new Set(ids);
  const items = allItems(s).filter((i) => chosen.has(i.id));
  return { pods: items.filter((i) => i.kind === "pod").length, files: items.filter((i) => i.kind === "files").length };
}

/** "3 tool pods and the collected files of 1 run" */
export function describeSelection({ pods, files }: { pods: number; files: number }): string {
  const parts = [
    pods && `${pods} tool pod${pods === 1 ? "" : "s"}`,
    files && `the collected files of ${files} run${files === 1 ? "" : "s"}`,
  ].filter(Boolean);
  return parts.join(" and ") || "nothing";
}

export const hasPendingPods = (s: CleanupStatus): boolean =>
  allItems(s).some((i) => i.kind === "pod" && !i.kept && !i.active);

export const keptCount = (s: CleanupStatus): number => allItems(s).filter((i) => i.kept).length;

export interface Banner { kind: "ok" | "info" | "warn"; title: string; body: string }

/** The line that tells the engineer what state the environment is in. */
export function banner(s: CleanupStatus): Banner {
  switch (s.state) {
    case "clean":
      return { kind: "ok", title: "Environment clean", body: "No tool pods or leftover files remain." };
    case "preserved": {
      const n = keptCount(s);
      return { kind: "info", title: "Left in place on purpose", body: `${n} item${n === 1 ? "" : "s"} kept for later. Nothing else remains.` };
    }
    case "busy":
      return { kind: "warn", title: "Something is still running", body: "A run or a scan is using tools right now, so they can't be removed. Nothing else needs cleaning." };
    case "unchecked":
      return { kind: "warn", title: "Couldn't check the cluster", body: `${s.pods_error ?? "The cluster can't be reached."} Leftover files were checked; tool pods were not.` };
    default:
      return { kind: "warn", title: "Leftovers need a choice", body: `${s.pending_count} item${s.pending_count === 1 ? " is" : "s are"} still on the system.` };
  }
}

const STATUS_TEXT: Record<string, string> = {
  interrupted: "interrupted", cleanup_failed: "cleanup failed", running: "in progress", queued: "queued",
  done: "finished", partial: "finished with errors", failed: "failed",
};
export const groupTitle = (g: CleanupGroup): string =>
  g.job_id === "adhoc" ? "Started outside a run"
  : `Run ${g.job_id.slice(0, 8)}${g.status ? `, ${STATUS_TEXT[g.status] ?? g.status}` : ""}`;

/** "Removed 2, kept 1, 1 could not be removed." */
export function outcomeSummary(outcomes: CleanupOutcome[]): string {
  const n = (r: CleanupOutcome["result"]) => outcomes.filter((o) => o.result === r).length;
  const parts = [
    n("removed") && `Removed ${n("removed")}`,
    n("kept") && `Kept ${n("kept")} for later`,
    n("skipped") && `${n("skipped")} skipped`,
    n("failed") && `${n("failed")} could not be removed`,
  ].filter(Boolean);
  return parts.length ? `${parts.join(", ")}.` : "Nothing changed.";
}
