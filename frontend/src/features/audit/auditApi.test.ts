import { describe, expect, it } from "vitest";
import { actionLabel, auditParams, emptyAuditFilter, latestOffset, shortSession } from "./auditApi";

describe("auditParams", () => {
  const now = new Date("2026-11-03T12:00:00.000Z");
  it("sends only paging when no filter is set", () => {
    expect(auditParams(emptyAuditFilter(), 50, 0, now).toString()).toBe("limit=50&offset=0");
  });
  it("trims fields and maps the session to its own parameter", () => {
    const p = auditParams({ actor: " neha ", action: "login", sessionId: "abc", range: "any" }, 50, 100, now);
    expect(p.get("actor")).toBe("neha");
    expect(p.get("action")).toBe("login");
    expect(p.get("session_id")).toBe("abc");
    expect(p.get("offset")).toBe("100");
  });
  it("turns the range into an exact UTC start time", () => {
    expect(auditParams({ ...emptyAuditFilter(), range: "hour" }, 50, 0, now).get("since")).toBe("2026-11-03T11:00:00.000Z");
    expect(auditParams({ ...emptyAuditFilter(), range: "day" }, 50, 0, now).get("since")).toBe("2026-11-02T12:00:00.000Z");
  });
});

describe("latestOffset", () => {
  it("is the start of the last page", () => {
    expect(latestOffset(0, 50)).toBe(0);
    expect(latestOffset(50, 50)).toBe(0);
    expect(latestOffset(51, 50)).toBe(50);
    expect(latestOffset(100, 50)).toBe(50);
    expect(latestOffset(101, 50)).toBe(100);
  });
});

describe("labels", () => {
  it("names known actions and keeps unknown ones readable", () => {
    expect(actionLabel("logs_privacy_scan")).toBe("PHI/PII scan");
    expect(actionLabel("some_new_action")).toBe("some new action");
  });
  it("shortens session ids", () => {
    expect(shortSession("abcdefghijklmnop")).toBe("abcdefgh");
    expect(shortSession(null)).toBe("—");
  });
});
