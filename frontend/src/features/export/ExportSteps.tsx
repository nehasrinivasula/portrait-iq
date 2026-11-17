import type { ExportStep } from "./exportApi";
import { STEP_MARK, STEP_STATUS_TEXT, stepPosition } from "./exportView";

/** Every export step with what it did. A step with problems opens to list each one and why;
 * failed steps start open so the reason is never hidden. */
export default function ExportSteps({ steps, live }: { steps: ExportStep[]; live: boolean }) {
  const { position, total } = stepPosition(steps);
  return (
    <div className="export-steps">
      {live && position && <p className="muted">Step {position} of {total}</p>}
      <ol>
        {steps.map((s) => (
          <li key={s.id} data-state={s.status} aria-current={s.status === "running" ? "step" : undefined}>
            <div className="export-step-head">
              <span className="export-mark" aria-hidden="true">{STEP_MARK[s.status]}</span>
              <span className="export-step-label">
                {s.label}
                <span className="visually-hidden">: {STEP_STATUS_TEXT[s.status]}</span>
              </span>
              {(s.status === "warn" || s.status === "failed") && (
                <span className={`export-tag ${s.status}`}>{s.status === "failed" ? "Failed" : "Notes"}</span>
              )}
            </div>
            {s.detail && <p className="export-step-detail">{s.detail}</p>}
            {s.issues.length > 0 && (
              <details className="export-issues" open={s.status === "failed"}>
                <summary>
                  {s.status === "failed" ? "What went wrong" : `${s.issues.length} note${s.issues.length === 1 ? "" : "s"}`}
                </summary>
                <ul>
                  {s.issues.map((i, n) => (
                    <li key={`${n}-${i.item}`}><strong>{i.item}</strong> — {i.reason}</li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
