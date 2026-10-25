import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, setUnauthorizedHandler, tokenStore } from "./api";
import { reasonFor } from "./features/login/LoginPage";

describe("reasonFor (login error messages)", () => {
  it("maps backend error codes and failures to specific messages", () => {
    expect(reasonFor(new ApiError(401, "x", "invalid_credentials")).title).toBe("Incorrect username or password");
    expect(reasonFor(new ApiError(503, "x", "not_configured")).kind).toBe("warn");
    expect(reasonFor(new ApiError(0, "x", "network")).title).toBe("Can't reach the server");
    expect(reasonFor(new ApiError(500, "x")).title).toBe("Server error");
  });
});

describe("apiFetch 401 handling", () => {
  const store = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
  afterEach(() => {
    store.clear();
    setUnauthorizedHandler(null);
  });

  it("signs the user out when a request with a token gets 401", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    tokenStore.set("old-token");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "expired", code: "token_invalid" }), { status: 401 }),
    ));

    await expect(apiFetch("/api/auth/me")).rejects.toMatchObject({ status: 401, code: "token_invalid" });
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });
});
