/**
 * The active diagnostic run is deliberately separate from the login token.
 * Signing out removes the token, but retains this opaque job id so the same
 * browser session can resume the run after the engineer signs in again.
 */
export const ACTIVE_JOB_STORAGE_KEY = "piq.job";

export const activeJobStore = {
  get: () => {
    try { return sessionStorage.getItem(ACTIVE_JOB_STORAGE_KEY); }
    catch { return null; }
  },
  set: (id: string) => {
    try { sessionStorage.setItem(ACTIVE_JOB_STORAGE_KEY, id); }
    catch { /* private browsing can disable storage; the server job still runs */ }
  },
  clear: () => {
    try { sessionStorage.removeItem(ACTIVE_JOB_STORAGE_KEY); }
    catch { /* ignore unavailable browser storage */ }
  },
};

/** Only a newly authenticated user should trigger the resume request. */
export const resumeJobId = (authenticated: boolean): string | null =>
  authenticated ? activeJobStore.get() : null;
