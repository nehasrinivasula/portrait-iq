import { describe, expect, it } from "vitest";
import { ACTION_OPTIONS, actionLabel, auditParams, emptyAuditFilter, latestOffset, shortSession, withRange } from "./auditApi";

describe("auditParams", () => {
  const now = new Date("2026-11-03T12:00:00.000Z");
  it("sends only paging when no filter is set", () => {
    expect(auditParams(emptyAuditFilter(), 50, 0).toString()).toBe("limit=50&offset=0");
  });
  it("trims fields and maps the session to its own parameter", () => {
    const p = auditParams({ actor: " neha ", action: "login", sessionId: "abc", range: "any", since: null }, 50, 100);
    expect(p.get("actor")).toBe("neha");
    expect(p.get("action")).toBe("login");
    expect(p.get("session_id")).toBe("abc");
    expect(p.get("offset")).toBe("100");
  });
  it("turns the range into an exact UTC start time, once", () => {
    const hour = withRange({ ...emptyAuditFilter(), range: "hour" }, now);
    expect(hour.since).toBe("2026-11-03T11:00:00.000Z");
    expect(withRange({ ...emptyAuditFilter(), range: "day" }, now).since).toBe("2026-11-02T12:00:00.000Z");
    expect(withRange(emptyAuditFilter(), now).since).toBe(null);
    // paging later keeps the same start time, however long it takes
    expect(auditParams(hour, 50, 50).get("since")).toBe("2026-11-03T11:00:00.000Z");
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

describe("ACTION_OPTIONS", () => {
  it("offers every action by its readable name, with the stored code as the value", () => {
    expect(ACTION_OPTIONS).toContainEqual(["login", "Sign in"]);
    const labels = ACTION_OPTIONS.map(([, l]) => l);
    expect(labels).toEqual([...labels].sort((a, b) => a.localeCompare(b)));
  });
});
