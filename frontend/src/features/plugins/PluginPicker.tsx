import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import * as Checkbox from "@radix-ui/react-checkbox";
import * as Label from "@radix-ui/react-label";
import { ApiError } from "../../api";
import { acquireForJob, listSources, type AcquisitionResult } from "../logs/logsApi";
import TimeFramePicker from "../logs/TimeFramePicker";
import { defaultTimeFrame, resolveTimeFrame, toApi } from "../logs/timeframe";
import { listPlugins, startJob, uploadPcap, type Job, type Plugin } from "./api";
import { RtiOptions, TsharkOptions } from "./ToolOptions";
import { buildOptions, defaultChoices, validateChoices, validateTargetIp } from "./options";
import "./plugins.css";

// Not offered here: hub_import is only a demo path, and lives on the Logs page.
const HIDDEN = new Set(["hub_import"]);
// The log export API is the source a run pulls from.
const LOG_SOURCE = "axone";

const Check = () => (
  <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
    <path d="M3 8.5l3.2 3.2L13 4.8" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/** A message the field engineer can act on from a failed request. */
const messageFor = (err: unknown) =>
  err instanceof ApiError
    ? err.status === 0 ? "Can't reach the server. Check the connection and try again."
      : typeof err.message === "string" ? err.message : "The server rejected the request."
    : "Something went wrong. Try again.";

export default function PluginPicker({ onStarted, locked }: {
  onStarted: (job: Job, pullLogs?: () => Promise<AcquisitionResult>) => void;
  /** A run is in progress: the form stays visible but can't start another. */
  locked: boolean;
}) {
  const uid = useId();
  const [plugins, setPlugins] = useState<Plugin[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [skipped, setSkipped] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [choices, setChoices] = useState(defaultChoices);
  const [targetIp, setTargetIp] = useState("");
  const [logsOn, setLogsOn] = useState(true);
  const [logsReason, setLogsReason] = useState<string | null>(null); // why logs can't be collected here
  const [frame, setFrame] = useState(defaultTimeFrame);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadName, setUploadName] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const feedback = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listPlugins()
      .then((r) => { setPlugins(r.plugins.filter((p) => !HIDDEN.has(p.name))); setSkipped(r.load_errors); })
      .catch((e) => setLoadError(messageFor(e)));
    listSources()
      .then((s) => {
        const src = s.find((x) => x.id === LOG_SOURCE);
        if (!src?.configured) { setLogsReason(src?.reason || "The log export API isn't set up on this server."); setLogsOn(false); }
      })
      .catch(() => { setLogsReason("Couldn't check the log sources."); setLogsOn(false); });
  }, []);

  // The start button is at the bottom of a long form: bring a failure into view.
  useEffect(() => {
    if (startError) feedback.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [startError]);

  const ipError = validateTargetIp(targetIp);
  const fieldErrors = useMemo(() => validateChoices(selected, choices), [selected, choices]);
  const window = useMemo(() => resolveTimeFrame(frame), [frame]);
  const nothingToDo = selected.size === 0 && !logsOn;
  const valid = !ipError && !nothingToDo && Object.keys(fieldErrors).length === 0 && !uploading && (!logsOn || window.ok);

  const toggle = (name: string, on: boolean) =>
    setSelected((s) => { const n = new Set(s); on ? n.add(name) : n.delete(name); return n; });
  const allOn = !!plugins?.length && plugins.every((p) => selected.has(p.name));
  const selectAll = () => setSelected(allOn ? new Set() : new Set(plugins?.map((p) => p.name)));

  async function onFile(file: File | undefined) {
    if (!file) return;
    setUploadError(null);
    setUploading(true);
    try {
      const res = await uploadPcap(file);
      setUploadName(file.name);
      setChoices((c) => ({ ...c, tshark: { ...c.tshark, uploadId: res.upload_id } }));
    } catch (e) {
      setUploadName(null);
      setChoices((c) => ({ ...c, tshark: { ...c.tshark, uploadId: null } }));
      setUploadError(messageFor(e));
    } finally {
      setUploading(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    setStartError(null);
    if (!valid || locked) return;
    setBusy(true);
    try {
      const job = await startJob(targetIp.trim(), [...selected], buildOptions(selected, choices));
      const w = logsOn && window.ok ? toApi(window) : null;
      onStarted(job, w ? () => acquireForJob(LOG_SOURCE, job.id, w.since, w.until) : undefined);
    } catch (err) {
      setStartError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  const n = selected.size;
  const label = busy ? "Starting…"
    : n > 0 && logsOn ? `Run ${n} diagnostic${n === 1 ? "" : "s"} and collect logs`
    : n > 0 ? `Run ${n} diagnostic${n === 1 ? "" : "s"}`
    : logsOn ? "Collect logs"
    : "Run diagnostics";

  return (
    <form className="picker" onSubmit={onSubmit} noValidate>
      <section aria-labelledby={`${uid}-target`}>
        <h2 id={`${uid}-target`}>Where to run it</h2>
        <div className="field">
          <Label.Root htmlFor={`${uid}-ip`}>Portrait Mobile server IP address</Label.Root>
          <input id={`${uid}-ip`} inputMode="decimal" autoComplete="off" value={targetIp} aria-invalid={submitted && !!ipError}
            aria-describedby={submitted && ipError ? `${uid}-ip-err` : `${uid}-ip-hint`}
            onChange={(e) => setTargetIp(e.target.value)} />
          {submitted && ipError ? <div className="error-text" id={`${uid}-ip-err`} role="alert">{ipError}</div>
            : <div className="hint" id={`${uid}-ip-hint`}>For example 192.168.0.3.</div>}
        </div>
      </section>

      <section aria-labelledby={`${uid}-logs`}>
        <h2 id={`${uid}-logs`}>Logs to include</h2>
        <div className="plugin-head">
          <Checkbox.Root id={`${uid}-logs-on`} className="check" checked={logsOn} disabled={!!logsReason}
            onCheckedChange={(v) => setLogsOn(v === true)}>
            <Checkbox.Indicator><Check /></Checkbox.Indicator>
          </Checkbox.Root>
          <Label.Root htmlFor={`${uid}-logs-on`} className="plugin-label">
            <span className="plugin-name">Collect system logs with this run</span>
            <span className="muted">{logsReason ?? "Pulled from the log export API and added to the log table automatically."}</span>
          </Label.Root>
        </div>
        {logsOn && <div style={{ marginLeft: 42, marginTop: 10 }}><TimeFramePicker value={frame} onChange={setFrame} legend="Time frame" /></div>}
      </section>

      <section aria-labelledby={`${uid}-tools`}>
        <div className="section-head">
          <h2 id={`${uid}-tools`}>Choose what to run</h2>
          {plugins && plugins.length > 0 && <button type="button" className="btn ghost" onClick={selectAll}>{allOn ? "Clear all" : "Select all"}</button>}
        </div>
        <p className="muted">Each diagnostic is removed from the system when it finishes.</p>

        {loadError && <div className="alert error" role="alert"><strong>Couldn't load the diagnostics</strong>{loadError}</div>}
        {!plugins && !loadError && <p className="muted" role="status">Loading diagnostics…</p>}

        <ul className="plugin-list">
          {plugins?.map((p) => {
            const on = selected.has(p.name);
            return (
              <li key={p.name} className={`plugin-card${on ? " on" : ""}`}>
                <div className="plugin-head">
                  <Checkbox.Root id={`${uid}-${p.name}`} className="check" checked={on} onCheckedChange={(v) => toggle(p.name, v === true)}>
                    <Checkbox.Indicator><Check /></Checkbox.Indicator>
                  </Checkbox.Root>
                  <Label.Root htmlFor={`${uid}-${p.name}`} className="plugin-label">
                    <span className="plugin-name">{p.display_name}</span>
                    <span className="muted">{p.description}</span>
                  </Label.Root>
                </div>
                {on && p.name === "tshark" && (
                  <TsharkOptions uid={uid} choices={choices} onChoices={setChoices} errors={fieldErrors}
                    upload={{ name: uploadName, busy: uploading, error: uploadError, onFile }} />
                )}
                {on && p.name === "rti" && <RtiOptions uid={uid} choices={choices} onChoices={setChoices} errors={fieldErrors} />}
              </li>
            );
          })}
        </ul>

        {Object.entries(skipped).map(([name, why]) => (
          <div key={name} className="alert warn" role="status"><strong>{name} is unavailable</strong>{why}</div>
        ))}
        {submitted && nothingToDo && <div className="error-text" role="alert">Choose at least one diagnostic, or include logs.</div>}
      </section>

      <div ref={feedback} />
      {submitted && !valid && !nothingToDo && (
        <div className="alert warn" role="alert"><strong>Some settings need attention</strong>Check the fields marked in red above.</div>
      )}
      {startError && <div className="alert error" role="alert"><strong>Couldn't start the run</strong>{startError}</div>}
      <button className="btn" type="submit" disabled={busy || uploading || locked}>{label}</button>
      {locked && <p className="muted" role="status">A run is in progress. You can start another when it finishes.</p>}
    </form>
  );
}
