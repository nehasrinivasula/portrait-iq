import { ApiError, apiFetch } from "../../api";

// Background tasks (log pulls and PHI/PII scans): POST starts one and returns at once, then
// GET /api/tasks/{id} reports progress until it ends. The result has the shape the old blocking call returned.

export type TaskState = "queued" | "running" | "done" | "failed" | "interrupted";
export type TaskKind = "acquire" | "privacy_scan";

export interface TaskStatus<R = unknown> {
  id: string;
  kind: string;
  status: TaskState;
  stage: string | null;
  done: number;
  total: number | null;
  result: R | null;
  error: string | null;
}

export type TaskProgress = (t: TaskStatus) => void;

const ENDED: TaskState[] = ["done", "failed", "interrupted"];
export const isEnded = (t: Pick<TaskStatus, "status">) => ENDED.includes(t.status);

/** "Scanning events: 1,200 of 5,000", or just the stage when the total is not known yet. */
export function progressText(t: Pick<TaskStatus, "stage" | "done" | "total" | "status">): string {
  if (t.status === "queued") return "Waiting to start…";
  const stage = t.stage ?? "Working";
  return t.total ? `${stage}: ${t.done.toLocaleString()} of ${t.total.toLocaleString()}` : `${stage}…`;
}

const POLL_MS = 1000;
const MAX_POLL_ERRORS = 5;
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * Follow a task until it ends. Resolves with its result; rejects with the server's message when it failed or was
 * interrupted (a restart). A few failed polls in a row are tolerated, since the point of this feature is that the
 * server may be slow, but a 401/423 (signed out, locked) is not retried.
 */
export async function waitForTask<R>(
  taskId: string,
  onProgress?: TaskProgress,
  opts: { pollMs?: number; fetchTask?: (id: string) => Promise<TaskStatus<R>>; wait?: (ms: number) => Promise<void> } = {},
): Promise<R> {
  const fetchTask = opts.fetchTask ?? ((id: string) => apiFetch<TaskStatus<R>>(`/api/tasks/${encodeURIComponent(id)}`));
  const wait = opts.wait ?? sleep;
  const pollMs = opts.pollMs ?? POLL_MS;
  let errors = 0;
  for (;;) {
    let t: TaskStatus<R>;
    try {
      t = await fetchTask(taskId);
      errors = 0;
    } catch (e) {
      const fatal = e instanceof ApiError && e.status !== 0 && e.status < 500;
      if (fatal || ++errors >= MAX_POLL_ERRORS) throw e;
      await wait(pollMs);
      continue;
    }
    onProgress?.(t);
    if (t.status === "done") return t.result as R;
    if (t.status === "failed") throw new ApiError(500, t.error ?? "The task failed.", "task_failed");
    if (t.status === "interrupted") {
      throw new ApiError(500, t.error ?? "The task was interrupted by a restart. Start it again.", "task_interrupted");
    }
    await wait(pollMs);
  }
}

/** Start a task with a POST, then follow it. */
export async function runTask<R>(start: () => Promise<TaskStatus<R>>, onProgress?: TaskProgress): Promise<R> {
  const t = await start();
  onProgress?.(t);
  return waitForTask<R>(t.id, onProgress);
}

/** Tasks still queued or running, newest first. Used to pick a scan back up after a page reload. */
export const activeTasks = (kind: TaskKind) =>
  apiFetch<TaskStatus[]>(`/api/tasks?active=true&kind=${kind}`);
