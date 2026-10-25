import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { tokenStore } from "./api";
import {
  isAccepted, statusText, summarize, uploadLogs, type Acquisition, type FileOutcome,
} from "./features/ingestion/ingestion";

const file = (status: FileOutcome["status"], extra: Partial<FileOutcome> = {}): FileOutcome =>
  ({ name: "logs.csv", status, size: 100, events: null, error: null, ...extra });

const result = (files: FileOutcome[], counts: Partial<Acquisition> = {}): Acquisition => ({
  run_id: "r1", source: "local", new_files: 0, already_acquired: 0, failed: 0, over_budget: 0,
  bytes_staged: 0, files, ...counts,
});

describe("statusText (plain words for every backend status)", () => {
  it("explains each outcome", () => {
    expect(statusText(file("ingested", { events: 548303 })).text).toBe("Imported, 548,303 log rows");
    expect(statusText(file("ingested", { events: 1 })).text).toBe("Imported, 1 log row");
    expect(statusText(file("already_acquired")).tone).toBe("skip");
    expect(statusText(file("duplicate")).text).toMatch(/Same content/);
    expect(statusText(file("unrecognized")).tone).toBe("warn");
    expect(statusText(file("over_budget")).text).toMatch(/size limit/);
    expect(statusText(file("failed", { error: "not a valid ZIP file" }))).toEqual({
      text: "Failed: not a valid ZIP file", tone: "bad",
    });
  });
});

describe("summarize (the result banner)", () => {
  it("reports new files and rows", () => {
    const s = summarize(result([file("ingested", { events: 548303 })], { new_files: 1 }));
    expect(s).toEqual({ tone: "ok", headline: "Imported 1 new file, 548,303 log rows." });
  });

  it("says plainly when nothing was new", () => {
    const s = summarize(result([file("already_acquired")], { already_acquired: 1 }));
    expect(s.tone).toBe("neutral");
    expect(s.headline).toMatch(/^Nothing new/);
  });

  it("flags failures and unrecognised formats", () => {
    const s = summarize(result(
      [file("ingested", { events: 2 }), file("unrecognized"), file("failed", { error: "x" })],
      { new_files: 2, failed: 1 },
    ));
    expect(s.tone).toBe("bad");
    expect(s.headline).toBe("Imported 2 new files, 2 log rows. 1 not in a recognised log format. 1 file failed.");
  });

  it("handles an empty result", () => {
    expect(summarize(result([])).headline).toMatch(/No log files/);
  });
});

describe("isAccepted", () => {
  it("allows log files and ZIPs only", () => {
    expect(["a.csv", "B.ZIP", "pod.log", "notes.txt"].every(isAccepted)).toBe(true);
    expect(["photo.jpg", "report.pdf", "csv"].some(isAccepted)).toBe(false);
  });
});

describe("uploadLogs", () => {
  const store = new Map<string, string>();
  beforeEach(() => {
    vi.stubGlobal("sessionStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    });
  });
  afterEach(() => {
    store.clear();
    vi.unstubAllGlobals();
  });

  it("sends the files as multipart with the bearer token", async () => {
    tokenStore.set("tok");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(result([]))));
    vi.stubGlobal("fetch", fetchMock);
    await uploadLogs([new File(["a,b"], "one.csv"), new File(["c,d"], "two.zip")]);

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/sources/local/upload");
    expect(init.method).toBe("POST");
    const headers = init.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer tok");
    expect(headers.get("Content-Type")).toBeNull();   // the browser adds the multipart boundary
    const sent = (init.body as FormData).getAll("files") as File[];
    expect(sent.map((f) => f.name)).toEqual(["one.csv", "two.zip"]);
  });

  it("surfaces the backend's reason when an upload is rejected", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "'photo.jpg' is not a supported log file" }), { status: 400 }),
    ));
    await expect(uploadLogs([new File(["x"], "photo.jpg")])).rejects.toMatchObject({
      status: 400, message: "'photo.jpg' is not a supported log file",
    });
  });
});
