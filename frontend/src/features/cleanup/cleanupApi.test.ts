import { describe, expect, it } from "vitest";
import { ACTION_OPTIONS, actionLabel } from "../audit/auditApi";
import {
  allItems, banner, groupTitle, keptCount, outcomeSummary, pendingIds, podIds,
  type CleanupItem, type CleanupStatus,
} from "./cleanupApi";

const item = (over: Partial<CleanupItem>): CleanupItem =>
  ({ id: "pod:a", kind: "pod", label: "a", detail: "", kept: false, active: false, ...over });

const status = (over: Partial<CleanupStatus> = {}): CleanupStatus => ({
  state: "pending", pods_error: null, interrupted_runs: 1, pending_count: 3,
  groups: [
    { job_id: "0123456789abcdef", status: "interrupted", items: [
      item({ id: "pod:p1" }), item({ id: "files:0123456789abcdef", kind: "files", label: "Collected files" }),
    ] },
    { job_id: "adhoc", status: null, items: [
      item({ id: "pod:kept", kept: true }), item({ id: "pod:live", active: true }), item({ id: "pod:p2" }),
    ] },
  ],
  ...over,
});

describe("choosing items", () => {
  it("lists every item across runs", () => expect(allItems(status())).toHaveLength(5));
  it("removes all deployments: every removable pod, never one in use, files left alone", () => {
    expect(podIds(status())).toEqual(["pod:p1", "pod:kept", "pod:p2"]);
  });
  it("keeps for later only what is still undecided and not in use", () => {
    expect(pendingIds(status())).toEqual(["pod:p1", "files:0123456789abcdef", "pod:p2"]);
  });
  it("counts kept items", () => expect(keptCount(status())).toBe(1));
});

describe("banner", () => {
  it("shows the final clean state", () => {
    expect(banner(status({ state: "clean", groups: [], pending_count: 0 }))).toMatchObject({ kind: "ok", title: "Environment clean" });
  });
  it("says preserved items were left on purpose", () => {
    const b = banner(status({ state: "preserved", pending_count: 0 }));
    expect(b.kind).toBe("info");
    expect(b.body).toMatch(/1 item kept for later/);
  });
  it("warns when a run is in progress, when leftovers need a choice and when the cluster was not checked", () => {
    expect(banner(status({ state: "busy" })).title).toMatch(/in progress/);
    expect(banner(status()).body).toBe("3 items are still on the system.");
    expect(banner(status({ pending_count: 1 })).body).toBe("1 item is still on the system.");
    expect(banner(status({ state: "unchecked", pods_error: "No Kubernetes access." })).body).toMatch(/No Kubernetes access\. Leftover files were checked/);
  });
});

describe("labels", () => {
  it("titles each run by its short id and status", () => {
    expect(groupTitle(status().groups[0])).toBe("Run 01234567, interrupted");
    expect(groupTitle(status().groups[1])).toBe("Started outside a run");
  });
  it("summarises what a choice did", () => {
    expect(outcomeSummary([
      { id: "a", result: "removed", reason: null }, { id: "b", result: "removed", reason: null },
      { id: "c", result: "kept", reason: null }, { id: "d", result: "failed", reason: "x" },
    ])).toBe("Removed 2, Kept 1 for later, 1 could not be removed.");
    expect(outcomeSummary([])).toBe("Nothing changed.");
  });
  it("the audit page can show and filter the new actions by name", () => {
    expect(actionLabel("cleanup_choice")).toBe("Cleanup choice");
    expect(ACTION_OPTIONS).toContainEqual(["logout", "Sign out"]);
    expect(ACTION_OPTIONS).toContainEqual(["leftovers_pending", "Leftovers waiting for a choice"]);
  });
});
