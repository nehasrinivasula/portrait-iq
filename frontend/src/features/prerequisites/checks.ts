export type CheckStatus = 'pass' | 'fail';

export interface CheckResult {
  id: string;
  label: string;
  status: CheckStatus;
  /** Present on fail: what is missing and how to fix it. */
  message?: string;
}

/** Demo toggle: when true, mocked results include failing items. */
export const SIMULATE_FAILURES = true;

export const CHECKS = [
  { id: 'docker', label: 'Docker daemon reachable' },
  { id: 'registry', label: 'Container registry access' },
  { id: 'capture', label: 'Capture permissions (tshark / pcap)' },
  { id: 'device', label: 'Device login credentials' },
  { id: 'token', label: 'Registry token' },
  { id: 'disk', label: 'Disk space for output ceiling' },
] as const;

const FAIL_MESSAGES: Record<string, string> = {
  capture:
    'tshark cannot open the capture interface. Run: sudo setcap cap_net_raw,cap_net_admin=eip $(which dumpcap), or add your user to the wireshark group, then re-run checks.',
  token: 'REGISTRY_TOKEN is not set. Generate a pull token in the registry and export REGISTRY_TOKEN.',
};
const SIMULATED_FAILS = new Set(['capture', 'token']);

/**
 * Intended contract (backend endpoint not built yet):
 *   GET /api/prerequisites?ids=docker,token -> 200 { "checks": CheckResult[] }
 * `ids` is optional (omitted = all checks); only the requested checks run, so
 * a slow or known-bad check can be skipped. Swap the body for a fetch.
 */
export async function runChecks(ids: string[]): Promise<CheckResult[]> {
  await new Promise((r) => setTimeout(r, 1200));
  return CHECKS.filter((c) => ids.includes(c.id)).map((c) =>
    SIMULATE_FAILURES && SIMULATED_FAILS.has(c.id)
      ? { id: c.id, label: c.label, status: 'fail', message: FAIL_MESSAGES[c.id] }
      : { id: c.id, label: c.label, status: 'pass' },
  );
}
