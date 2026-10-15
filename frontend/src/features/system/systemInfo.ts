export interface SystemInfo {
  hostname: string;
  boxId: string;
  appVersion: string;
  /** Device-facing capture interface; the real value comes from the host NIC list. */
  captureInterface: { name: string; address: string };
}

/** Demo toggle: no backend endpoint exists yet, so this returns mocked values. */
export const MOCK_SYSTEM_INFO: SystemInfo = {
  hostname: 'piq-box-01',
  boxId: 'PIQ-0001',
  appVersion: '0.1.0-demo',
  captureInterface: { name: 'eth1', address: '10.20.0.2/24' },
};

/**
 * Intended contract (backend endpoint not built yet):
 *   GET /api/system/info -> 200 SystemInfo
 * Must be reachable WITHOUT a token so the login screen can show it, so keep it
 * to non-sensitive fields. Server time is taken from the browser for now; the
 * real endpoint should also return `server_time` so skew is visible.
 */
export async function getSystemInfo(): Promise<SystemInfo> {
  await new Promise((r) => setTimeout(r, 150));
  return MOCK_SYSTEM_INFO;
}
