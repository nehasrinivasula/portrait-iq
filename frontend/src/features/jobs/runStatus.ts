import type { RunError } from "../plugins/api";

// Plain-language labels for the run's steps (SCRUM-68), shared by the run
// panel and the results page so a failing step reads the same everywhere.
const STEP_LABEL: Record<string, string> = {
  configure: "Checking the selected tools",
  deploy_tools: "Running the diagnostics",
  collect_server_logs: "Collecting logs",
  collect_network_diagnostics: "Collecting network information",
  collect_grafana_health: "Collecting system health",
  cleanup: "Removing diagnostics from the system",
  package: "Preparing results",
};

/** A step code as a readable phrase; an unknown code is shown as-is. */
export const stepLabel = (step: string | null | undefined): string | null =>
  step ? STEP_LABEL[step] ?? step : null;

/** One sentence for a run-level failure: where it failed, which tool, and why. */
export function runErrorText(error: RunError): string {
  const where = stepLabel(error.step);
  const lead = error.plugin
    ? (where ? `${error.plugin} failed while ${where.toLowerCase()}` : `${error.plugin} failed`)
    : (where ? `Failed while ${where.toLowerCase()}` : "The run failed");
  return `${lead}: ${error.reason}`;
}
