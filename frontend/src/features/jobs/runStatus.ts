import type { RunError } from "../plugins/api";

// Plain-language step labels come from plugins/progress (SCRUM-41); "configure"
// can fail before the run starts, so it is labelled here (SCRUM-68).
import { STEP_LABEL } from "../plugins/progress";

/** A step code as a readable phrase; an unknown code is shown as-is. */
export const stepLabel = (step: string | null | undefined): string | null =>
  step ? (step === "configure" ? "Checking the selected tools" : STEP_LABEL[step] ?? step) : null;

/** One sentence for a run-level failure: where it failed, which tool, and why. */
export function runErrorText(error: RunError): string {
  const where = stepLabel(error.step);
  const lead = error.plugin
    ? (where ? `${error.plugin} failed while ${where.toLowerCase()}` : `${error.plugin} failed`)
    : (where ? `Failed while ${where.toLowerCase()}` : "The run failed");
  return `${lead}: ${error.reason}`;
}
