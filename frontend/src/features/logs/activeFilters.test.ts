import { describe, expect, it } from "vitest";
import { activeFilters, removeFilter } from "./activeFilters";
import { emptyFilter } from "./logsApi";
import { defaultTimeFrame, type TimeFrameChoice } from "./timeframe";

const anyTime: TimeFrameChoice = { ...defaultTimeFrame(), preset: "any" };

describe("activeFilters", () => {
  it("lists exactly the filters the engineer picked", () => {
    const filter = { ...emptyFilter(), sources: ["portrait"], severities: ["WARN" as const, "ERROR" as const],
      column: "category" as const, value: " PerfData ", q: "timeout" };
    expect(activeFilters({ ...anyTime, preset: "24h" }, filter)).toEqual([
      { id: "date", label: "Date", value: "Last 24 hours" },
      { id: "source:portrait", label: "Source", value: "portrait" },
      { id: "severity:WARN", label: "Level", value: "WARN" },
      { id: "severity:ERROR", label: "Level", value: "ERROR" },
      { id: "value", label: "Category", value: "PerfData" },
      { id: "q", label: "Message has", value: "timeout" },
    ]);
  });

  it("shows nothing when cleared", () => {
    expect(activeFilters(anyTime, emptyFilter())).toEqual([]);
  });
});

describe("removeFilter", () => {
  it("drops one condition and keeps the rest", () => {
    const filter = { ...emptyFilter(), severities: ["WARN" as const, "ERROR" as const], q: "timeout" };
    const frame = { ...anyTime, preset: "7d" as const };

    const noWarn = removeFilter("severity:WARN", frame, filter);
    expect(noWarn.filter.severities).toEqual(["ERROR"]);
    expect(noWarn.filter.q).toBe("timeout");
    expect(noWarn.frame.preset).toBe("7d");

    expect(removeFilter("date", frame, filter).frame.preset).toBe("any");
    expect(removeFilter("q", frame, filter).filter.q).toBe("");
  });

  it("keeps a source name that itself has a colon", () => {
    const filter = { ...emptyFilter(), sources: ["a:b", "c"] };
    expect(removeFilter("source:a:b", anyTime, filter).filter.sources).toEqual(["c"]);
  });
});
