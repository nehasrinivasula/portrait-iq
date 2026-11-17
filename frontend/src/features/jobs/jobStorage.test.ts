import { afterEach, describe, expect, it, vi } from "vitest";
import { activeJobStore, ACTIVE_JOB_STORAGE_KEY, resumeJobId } from "./jobStorage";

describe("active diagnostic run storage", () => {
  const values = new Map<string, string>();

  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    removeItem: (key: string) => void values.delete(key),
  });

  afterEach(() => values.clear());

  it("keeps the job id while signed out and restores it after authentication", () => {
    activeJobStore.set("job-123");

    expect(resumeJobId(false)).toBeNull();
    expect(sessionStorage.getItem(ACTIVE_JOB_STORAGE_KEY)).toBe("job-123");
    expect(resumeJobId(true)).toBe("job-123");
  });

  it("forgets the run only when the engineer explicitly clears it", () => {
    activeJobStore.set("job-123");
    activeJobStore.clear();
    expect(resumeJobId(true)).toBeNull();
  });
});
