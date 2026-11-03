// Per-check outcomes from tools that run several checks (Netshoot, SCRUM-91).
// The server sends a short fixed reason for a failed check, never raw command output.

export interface CheckOutcome {
  label: string;
  status: "pass" | "fail";
  reason: string | null;
}

export function checkSummary(checks: CheckOutcome[]): string {
  const passed = checks.filter((c) => c.status === "pass").length;
  return `${passed} of ${checks.length} checks passed`;
}
