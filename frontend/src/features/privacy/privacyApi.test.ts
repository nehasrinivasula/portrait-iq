import { describe, expect, it } from "vitest";
import { categoryLabel, noPrivacyOptions, privacyHeadline, privacyParams, type PrivacyReport } from "./privacyApi";

const report = (over: Partial<PrivacyReport> = {}): PrivacyReport => ({
  job_id: "unassigned", scanner_version: 1, events: 100, scanned_events: 100, unscanned_events: 0, all_scanned: true,
  flagged_events: 0, network_identifier_events: 0, network_by_category: [], by_category: [], by_confidence: [],
  by_field: [], files: [], limits: [], ...over,
});

describe("privacyParams", () => {
  it("leaves out switches that are off", () => {
    expect(privacyParams(undefined).toString()).toBe("");
    expect(privacyParams(noPrivacyOptions()).toString()).toBe("");
  });
  it("sends flagged and mask when on", () => {
    const p = privacyParams({ flaggedOnly: true, mask: true });
    expect(p.get("flagged")).toBe("true");
    expect(p.get("mask")).toBe("true");
  });
});

describe("privacyHeadline", () => {
  it("never claims clean data when events are unscanned", () => {
    expect(privacyHeadline(report({ all_scanned: false, unscanned_events: 5 }))).toBe("5 events have not been scanned yet.");
    expect(privacyHeadline(report({ all_scanned: false, unscanned_events: 1 }))).toBe("1 event has not been scanned yet.");
  });
  it("says so when nothing was found, and counts flagged events", () => {
    expect(privacyHeadline(report())).toBe("Scanned: no known PHI/PII patterns found.");
    expect(privacyHeadline(report({ flagged_events: 1 }))).toBe("Scanned: 1 event may contain PHI/PII.");
    expect(privacyHeadline(report({ flagged_events: 10 }))).toBe("Scanned: 10 events may contain PHI/PII.");
  });
  it("handles an empty collection", () => {
    expect(privacyHeadline(report({ events: 0, scanned_events: 0 }))).toBe("No logs in this collection yet.");
  });
});

describe("categoryLabel", () => {
  it("names known kinds and falls back for new ones", () => {
    expect(categoryLabel("ssn")).toBe("Social Security number");
    expect(categoryLabel("new_kind")).toBe("new kind");
  });
});
