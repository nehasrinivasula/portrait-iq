import { describe, expect, it } from "vitest";
import type { ExportStep, Package } from "./exportApi";
import { formatBytes, headline, issueCount, returnTarget, scopeText, shortHash, stepPosition } from "./exportView";

const step = (id: string, status: ExportStep["status"], issues = 0): ExportStep => ({
  id, label: `Step ${id}`, status, detail: null,
  issues: Array.from({ length: issues }, (_, i) => ({ item: `x${i}`, reason: "because" })),
});

const pkg = (over: Partial<Package> = {}): Package => ({
  job_id: "job-1", state: "ready", masked: true, partial: false, requested_by: "t", started_at: "", finished_at: "",
  steps: [step("a", "done"), step("b", "done")], file_name: "x.zip", size_bytes: 10, sha256: "a".repeat(64),
  contents: [], warnings: [], error: null, downloadable: true, ...over,
});

describe("returnTarget", () => {
  it("goes back to the page the export was opened from", () => {
    expect(returnTarget("logs", "job 1")).toEqual({ to: "/logs?job=job%201", label: "Back to Logs" });
    expect(returnTarget("run", "j")).toEqual({ to: "/run", label: "Back to Run diagnostics" });
    expect(returnTarget(null, "j").to).toBe("/run");
    expect(returnTarget("logs", "all").to).toBe("/logs");
    expect(returnTarget("logs", "unassigned").to).toBe("/logs?job=unassigned");
  });
});

describe("scopeText", () => {
  it("names what is exported", () => {
    expect(scopeText("run", "abcdef0123").heading).toBe("Export run");
    expect(scopeText("run", "abcdef0123").about).toContain("Run abcdef01");
    expect(scopeText("imported", "unassigned").heading).toBe("Export imported files");
    expect(scopeText("all", "all").heading).toBe("Export all logs");
  });
});

describe("stepPosition", () => {
  it("counts the running step", () => {
    expect(stepPosition([step("a", "done"), step("b", "running"), step("c", "pending")])).toEqual({ position: 2, total: 3 });
  });
  it("has no position before the start or after the end", () => {
    expect(stepPosition([step("a", "pending")]).position).toBeNull();
    expect(stepPosition([step("a", "done"), step("b", "warn")]).position).toBeNull();
  });
});

describe("headline", () => {
  it("names the running step while preparing", () => {
    expect(headline(pkg({ state: "preparing", steps: [step("a", "running")] }))).toEqual(["info", "Preparing the zip", "Step a"]);
  });
  it("is ok only when nothing needs a look", () => {
    expect(headline(pkg())[0]).toBe("ok");
    expect(headline(pkg({ warnings: ["w"] }))[1]).toBe("Export ready, with notes");
  });
  it("is not ok when a step has notes, e.g. a diagnostic that did not run", () => {
    const [kind, title, body] = headline(pkg({ steps: [step("tools", "warn", 2), step("zip", "done")] }));
    expect([kind, title]).toEqual(["warn", "Export ready, with notes"]);
    expect(body).toContain("2 things to check");
  });
  it("says what is missing when a step failed", () => {
    const [kind, title, body] = headline(pkg({ partial: true, steps: [step("a", "failed"), step("b", "done")] }));
    expect([kind, title]).toEqual(["warn", "Export ready, but something is missing"]);
    expect(body).toContain("1 part could not be added");
  });
  it("shows the server's reason when the export failed", () => {
    expect(headline(pkg({ state: "failed", error: "No space left." }))).toEqual(["error", "The export failed", "No space left."]);
    expect(headline(pkg({ state: "interrupted", error: null }))[0]).toBe("warn");
  });
});

describe("formatting", () => {
  it("formats sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(300 * 1024 * 1024)).toBe("300 MB");
    expect(formatBytes(null)).toBe("—");
  });
  it("shortens hashes and counts issues", () => {
    expect(shortHash("abcdef0123456789")).toBe("abcdef012345…");
    expect(issueCount([step("a", "warn", 2), step("b", "failed", 1)])).toBe(3);
  });
});
