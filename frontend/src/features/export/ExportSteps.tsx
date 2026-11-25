import type { ExportStep } from "./exportApi";
import { parseServerTime } from "../plugins/progress";
import Steps from "../../ui/Steps";

/** The export's steps in the shared progress list. Failed steps open to say why. */
export default function ExportSteps({ steps, live }: { steps: ExportStep[]; live: boolean }) {
  return (
    <Steps live={live} steps={steps.map((s) => ({
      id: s.id, label: s.label, status: s.status, detail: s.detail, issues: s.issues,
      startedAt: parseServerTime(s.started_at), finishedAt: parseServerTime(s.finished_at),
    }))} />
  );
}
