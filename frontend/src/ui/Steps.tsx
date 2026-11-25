import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { isFinished, newlyFinished, STATUS_TEXT, stepDuration, stepPosition, totalDuration, type StepItem, type StepStatus } from "./stepModel";
import "./steps.css";

const STAGGER_MS = 60;   // steps finished between two polls are ticked off this far apart (under 0.5 s in all)

interface Props {
  steps: StepItem[];
  /** The job is still going: show "Step 2 of 5" and keep the list open. */
  live: boolean;
  /** Extra rows under a step, e.g. per-tool status under "Running the diagnostics". */
  under?: Record<string, ReactNode>;
  /** Once finished, fold the list into "6 steps · 4.1 s"; failed or noted steps keep it open. */
  collapsible?: boolean;
}

/** Shared progress list for runs and exports. Shows the real state the moment it arrives. */
export default function Steps({ steps, live, under = {}, collapsible = false }: Props) {
  const prev = useRef<Map<string, StepStatus> | null>(null);
  const fresh = newlyFinished(prev.current, steps);
  useEffect(() => { prev.current = new Map(steps.map((s) => [s.id, s.status])); });

  const { position, total } = stepPosition(steps);
  const list = (
    <ol className="steps">
      {steps.map((s) => {
        const k = fresh.indexOf(s.id);
        const took = stepDuration(s);
        return (
          <li key={s.id} data-state={s.status} data-fresh={k >= 0 || undefined}
            style={k >= 0 ? ({ "--stagger": `${k * STAGGER_MS}ms` } as CSSProperties) : undefined}
            aria-current={s.status === "running" ? "step" : undefined}>
            <span className="step-mark" aria-hidden="true" />
            <div className="step-body">
              <div className="step-head">
                <span className="step-label">{s.label}<span className="visually-hidden">: {STATUS_TEXT[s.status]}</span></span>
                {s.status === "warn" && <span className="step-tag warn">Notes</span>}
                {s.status === "partial" && <span className="step-tag partial">Partial</span>}
                {s.status === "failed" && <span className="step-tag failed">Failed</span>}
                {took && <span className="step-time">{took}</span>}
              </div>
              {s.detail && <p className="step-detail">{s.detail}</p>}
              {s.issues && s.issues.length > 0 && (
                <details className="step-issues" open={s.status === "failed" || s.status === "partial"}>
                  <summary>{s.status === "failed" || s.status === "partial" ? "What went wrong" : `${s.issues.length} note${s.issues.length === 1 ? "" : "s"}`}</summary>
                  <ul>{s.issues.map((i, n) => <li key={`${n}-${i.item}`}><strong>{i.item}</strong>: {i.reason}</li>)}</ul>
                </details>
              )}
              {under[s.id]}
            </div>
          </li>
        );
      })}
    </ol>
  );

  if (live) {
    return <div className="steps-wrap">{position && <p className="steps-count">Step {position} of {total}</p>}{list}</div>;
  }
  const needsLook = steps.some((s) => s.status === "failed" || s.status === "partial" || s.status === "warn");
  if (!collapsible || needsLook || !steps.every((s) => isFinished(s.status))) return <div className="steps-wrap">{list}</div>;
  const took = totalDuration(steps);
  return (
    <details className="steps-wrap steps-summary">
      <summary>{steps.length} step{steps.length === 1 ? "" : "s"}{took ? ` · ${took}` : ""}</summary>
      {list}
    </details>
  );
}
