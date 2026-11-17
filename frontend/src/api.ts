// Thin fetch wrapper. Adds the bearer token and normalizes errors to ApiError.
const TOKEN_KEY = "piq.token";

export const tokenStore = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  set: (t: string) => sessionStorage.setItem(TOKEN_KEY, t),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
};

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) {
    super(message);
  }
}

// Registered by AuthProvider: called when a request made with a token gets a 401
// (the token expired mid-session) or a 423 (the server locked the idle session,
// SCRUM-34) so the app can return to the login screen.
export type SignOutReason = "expired" | "locked";
let onUnauthorized: ((reason: SignOutReason) => void) | null = null;
export const setUnauthorizedHandler = (fn: ((reason: SignOutReason) => void) | null) => {
  onUnauthorized = fn;
};

async function send(path: string, init: RequestInit): Promise<Response> {
  const token = tokenStore.get();
  const headers = new Headers(init.headers);
  // JSON bodies are passed as strings. Anything else (FormData for file
  // uploads, Blob, URLSearchParams) gets its Content-Type from the browser;
  // forcing JSON there drops the multipart boundary and the upload fails.
  if (typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, "Cannot reach the server.", "network");
  }
  if (res.status === 401 && token) onUnauthorized?.("expired");
  if (res.status === 423 && token) onUnauthorized?.("locked");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? res.statusText, body.code);
  }
  return res;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await send(path, init);
  return res.json() as Promise<T>;
}

/** A file download that needs the bearer token, so a plain link cannot fetch it. */
export async function apiDownload(path: string): Promise<{ blob: Blob; fileName: string | null }> {
  const res = await send(path, {});
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  return { blob: await res.blob(), fileName: match ? decodeURIComponent(match[1]) : null };
}
