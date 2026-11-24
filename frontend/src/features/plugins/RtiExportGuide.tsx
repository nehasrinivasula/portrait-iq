/**
 * Step-by-step guide for the UMF RTI import mode (SCRUM-123). RTI Admin Console
 * and RTI Monitor are GUI-only and too heavy to run on the MiniEdge, so the
 * field engineer runs them on their own laptop (where GE's RTI tools are
 * already installed and set up to reach the system), exports the two files,
 * and uploads them here. Menu names checked against Admin Console and Monitor
 * 6.1.2 on the lab bench, 2026-11-24.
 */

export interface GuideStep {
  title: string;
  items: string[];
}

export const RTI_GUIDE_STEPS: GuideStep[] = [
  {
    title: "Export the Admin Console file (.xml)",
    items: [
      "Open RTI Admin Console on your laptop. If it asks about joining domains, choose to discover and join them automatically.",
      "Check that Domain 7, Domain 12 and Domain 13 are listed in the DDS Logical View on the left. If one is missing, open Preferences (the gear icon) and add it to the domains to join.",
      "Wait about a minute, until topics such as umfreg appear under Domain 12.",
      "Choose File ▸ Export Discovery Data. Leave every obfuscation option unchecked.",
      "On the Choose File page, enter where to save it, for example `discovery-data.xml` on your Desktop, then click Finish.",
    ],
  },
  {
    title: "Save the RTI Monitor file (.ser)",
    items: [
      "Open RTI Monitor on your laptop.",
      "At “Join DDS Domain”, enter 12 and click OK. Then use Domain ▸ Join Domain for 13, and again for 7.",
      "Wait about a minute, until Domains: 3 shows in the bar at the bottom.",
      "Choose File ▸ Save and save it, for example as `monitor-session.ser` on your Desktop.",
    ],
  },
  {
    title: "Upload the files here",
    items: [
      "Pick which export below and choose its file.",
      "To include both, add the other one in a second run.",
    ],
  },
];

/** What the laptop needs before step 1. */
export const RTI_GUIDE_NEEDS = "Connect your laptop to the system's network first.";

export const RTI_GUIDE_NOTES: string[] = [
  "RTI Monitor's statistics are expected to be empty for now: the tools can see the system but not its monitoring domain. The .ser still records which participants, topics, writers and readers exist.",
  "If Admin Console says “Errors have occurred”, choose No. The tools only listen, so Admin Console is not allowed to send commands; discovery and the export still work.",
];

/** Text with `backticked` names shown as code. */
function Rich({ text }: { text: string }) {
  return <>{text.split("`").map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part))}</>;
}

export default function RtiExportGuide() {
  return (
    <details className="guide">
      <summary>How to get these files</summary>
      <p>
        Both files come from GE's RTI tools on your laptop. <Rich text={RTI_GUIDE_NEEDS} />
      </p>
      <ol className="guide-steps">
        {RTI_GUIDE_STEPS.map((s) => (
          <li key={s.title}>
            <span className="guide-step-title">{s.title}</span>
            <ul>{s.items.map((i) => <li key={i}><Rich text={i} /></li>)}</ul>
          </li>
        ))}
      </ol>
      <ul className="guide-notes muted">
        {RTI_GUIDE_NOTES.map((n) => <li key={n}><Rich text={n} /></li>)}
      </ul>
    </details>
  );
}
