/**
 * Step-by-step guide for the UMF RTI import mode (SCRUM-123). RTI Admin Console
 * and RTI Monitor are GUI-only and too heavy to run on the MiniEdge, so the
 * field engineer runs GE's RTI tools image on their own laptop, exports the two
 * files, and uploads them here. The setup script (public/rti/start-rti-tools.sh)
 * applies the connection settings, so the steps below are only the GUI clicks.
 * Validated on the lab bench 2026-11-24 (Admin Console + Monitor 6.1.2).
 */

/** Where the downloadable setup script is served from (frontend/public). */
export const RTI_SETUP_SCRIPT = "/rti/start-rti-tools.sh";

/** Paths inside the tools desktop; the script maps this folder to ~/rti-out on the laptop. */
export const RTI_EXPORT_DIR = "/home/developer/rti-out";

export interface GuideStep {
  title: string;
  items: string[];
}

export const RTI_GUIDE_STEPS: GuideStep[] = [
  {
    title: "Start the RTI tools on your laptop",
    items: [
      "Download the setup script (link above) and run it in a terminal: `bash start-rti-tools.sh`",
      "It prints a VNC password and opens the tools desktop (on a Mac, in Screen Sharing). Elsewhere, open a VNC viewer at `localhost:5901`.",
      "Sign in to the desktop with that password.",
    ],
  },
  {
    title: "Export the Admin Console file (.xml)",
    items: [
      "Double-click Admin Console on the desktop. It joins domains 7, 12 and 13 by itself.",
      "Wait about a minute, until topics such as umfreg appear under Domain 12 on the left.",
      "Choose File ▸ Export Discovery Data. Leave every obfuscation option unchecked.",
      `On the Choose File page, save to \`${RTI_EXPORT_DIR}/discovery-data.xml\`, then click Finish.`,
    ],
  },
  {
    title: "Save the RTI Monitor file (.ser)",
    items: [
      "Double-click RTI Monitor on the desktop.",
      "At “Join DDS Domain”, enter 12 and click OK. Then use Domain ▸ Join Domain for 13, and again for 7.",
      "Wait about a minute, until Domains: 3 shows in the bar at the bottom.",
      `Choose File ▸ Save and save to \`${RTI_EXPORT_DIR}/monitor-session.ser\`.`,
    ],
  },
  {
    title: "Upload the files here",
    items: [
      "Both files are now in `~/rti-out` on the laptop.",
      "Pick which export below and choose its file. To include both, add the other one in a second run.",
      "When you are done, stop the tools: `bash start-rti-tools.sh stop`",
    ],
  },
];

/** What the laptop needs before step 1. */
export const RTI_GUIDE_NEEDS =
  "Connect your laptop to the system's network and make sure Docker is running.";

export const RTI_GUIDE_NOTES: string[] = [
  "RTI Monitor's statistics are expected to be empty for now: the tools can see the system but not its monitoring domain. The .ser still records which participants, topics, writers and readers exist.",
  "If a window says “Errors have occurred”, choose No. The tools only listen, so Admin Console is not allowed to send commands; discovery and the export still work.",
];

/** Text with `backticked` commands and paths shown as code. */
function Rich({ text }: { text: string }) {
  return <>{text.split("`").map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part))}</>;
}

export default function RtiExportGuide() {
  return (
    <details className="guide">
      <summary>How to get these files</summary>
      <p>
        RTI Admin Console and RTI Monitor run on your laptop, not on the system.{" "}
        <a href={RTI_SETUP_SCRIPT} download="start-rti-tools.sh">Download the setup script</a>{" "}
        to start them with the right connection settings.
      </p>
      <p><Rich text={RTI_GUIDE_NEEDS} /></p>
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
