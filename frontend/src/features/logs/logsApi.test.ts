import { describe, expect, it } from "vitest";
import { emptyFilter, filterParams } from "./logsApi";

describe("filterParams", () => {
  it("leaves out empty filters", () => {
    expect(filterParams(emptyFilter()).toString()).toBe("");
  });

  it("repeats multi-value keys and sends each field as its own parameter", () => {
    const p = filterParams({
      ...emptyFilter(), sources: ["portrait", "kubernetes"], severities: ["WARN", "ERROR"],
      device: "host-a", category: " PerfData ", process: "svc", sourceFile: "a.csv", q: "timeout", jobId: "j1", since: "2026-10-25T00:00:00.000Z",
    });
    expect(p.getAll("source")).toEqual(["portrait", "kubernetes"]);
    expect(p.getAll("severity")).toEqual(["WARN", "ERROR"]);
    expect(p.get("device")).toBe("host-a");
    expect(p.get("category")).toBe("PerfData");
    expect(p.get("process")).toBe("svc");
    expect(p.get("source_file")).toBe("a.csv");
    expect(p.get("q")).toBe("timeout");
    expect(p.get("job_id")).toBe("j1");
    expect(p.get("since")).toBe("2026-10-25T00:00:00.000Z");
  });
});
