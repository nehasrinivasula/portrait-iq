export type NetshootCheck = {
  label: string;
  status: "pass" | "fail";
  reason: string | null;
};

export type PluginResult = {
  tool: string;
  summary?: string;
  step?: string;
  error?: string;
  checks?: NetshootCheck[];
};

export function netshootSummary(checks: NetshootCheck[]): string {
  const passed = checks.filter((check) => check.status === "pass").length;
  return `${passed} of ${checks.length} network checks passed`;
}
