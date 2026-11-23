import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import { isEnded, progressText, runTask, waitForTask, type TaskStatus } from "./tasksApi";

const task = (over: Partial<TaskStatus> = {}): TaskStatus => ({
  id: "t1", kind: "privacy_scan", status: "running", stage: "Scanning events", done: 0, total: null, result: null, error: null, ...over,
});
const noWait = () => Promise.resolve();

describe("progressText", () => {
  it("shows counts when the total is known", () => {
    expect(progressText(task({ done: 1200, total: 5000 }))).toBe("Scanning events: 1,200 of 5,000");
  });
  it("shows only the stage before the total is known", () => {
    expect(progressText(task())).toBe("Scanning events…");
  });
  it("says it is waiting while queued", () => {
    expect(progressText(task({ status: "queued" }))).toBe("Waiting to start…");
  });
});

describe("isEnded", () => {
  it("is true for done, failed and interrupted only", () => {
    expect(["queued", "running"].map((status) => isEnded({ status } as TaskStatus))).toEqual([false, false]);
    expect(["done", "failed", "interrupted"].map((status) => isEnded({ status } as TaskStatus))).toEqual([true, true, true]);
  });
});

describe("waitForTask", () => {
  it("polls until done, reports progress, and resolves with the result", async () => {
    const seq = [task({ status: "queued" }), task({ done: 5, total: 10 }), task({ status: "done", result: { scanned: 10 } })];
    const fetchTask = vi.fn(async () => seq.shift()!);
    const seen: string[] = [];
    const r = await waitForTask<{ scanned: number }>("t1", (t) => seen.push(t.status), { fetchTask: fetchTask as never, wait: noWait });
    expect(r).toEqual({ scanned: 10 });
    expect(seen).toEqual(["queued", "running", "done"]);
  });

  it("rejects with the server's message when the task failed", async () => {
    const fetchTask = async () => task({ status: "failed", error: "Source unreachable." });
    await expect(waitForTask("t1", undefined, { fetchTask, wait: noWait })).rejects.toMatchObject({ message: "Source unreachable.", code: "task_failed" });
  });

  it("rejects when the task was interrupted by a restart", async () => {
    const fetchTask = async () => task({ status: "interrupted", error: null });
    await expect(waitForTask("t1", undefined, { fetchTask, wait: noWait })).rejects.toMatchObject({ code: "task_interrupted" });
  });

  it("rides out a few failed polls, then continues", async () => {
    let calls = 0;
    const fetchTask = async () => {
      if (++calls < 3) throw new ApiError(0, "Cannot reach the server.", "network");
      return task({ status: "done", result: "ok" });
    };
    await expect(waitForTask("t1", undefined, { fetchTask: fetchTask as never, wait: noWait })).resolves.toBe("ok");
  });

  it("gives up after repeated poll failures", async () => {
    const fetchTask = async () => { throw new ApiError(0, "Cannot reach the server.", "network"); };
    await expect(waitForTask("t1", undefined, { fetchTask, wait: noWait })).rejects.toMatchObject({ status: 0 });
  });

  it("does not retry a 4xx (signed out, locked, not found)", async () => {
    const fetchTask = vi.fn(async () => { throw new ApiError(423, "Session locked."); });
    await expect(waitForTask("t1", undefined, { fetchTask, wait: noWait })).rejects.toMatchObject({ status: 423 });
    expect(fetchTask).toHaveBeenCalledTimes(1);
  });
});

describe("runTask", () => {
  it("surfaces a start error without polling", async () => {
    await expect(runTask(async () => { throw new ApiError(422, "Bad window."); })).rejects.toMatchObject({ status: 422 });
  });
});
