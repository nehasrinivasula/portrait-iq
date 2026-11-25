import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { checkConnection, CONNECTION_PORTS, detectConnection } from "./connectionApi";

describe("connection setup API", () => {
  beforeEach(() => vi.stubGlobal("sessionStorage", {
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
  }));
  afterEach(() => vi.unstubAllGlobals());

  it("sends a bounded preflight with no credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ reachable: false }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await checkConnection("192.0.2.10");

    expect(fetchMock).toHaveBeenCalledWith("/api/connectivity-check", expect.objectContaining({ method: "POST" }));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ target: "192.0.2.10", ports: CONNECTION_PORTS, timeout_seconds: 3 });
  });

  it("posts credentials only to the request-scoped connect endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await detectConnection({ ip_address: "192.0.2.10", username: "engineer", password: "temporary-value" });

    expect(fetchMock).toHaveBeenCalledWith("/api/connect", expect.objectContaining({ method: "POST" }));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      ip_address: "192.0.2.10", username: "engineer", password: "temporary-value", mode: "live",
    });
  });
});
