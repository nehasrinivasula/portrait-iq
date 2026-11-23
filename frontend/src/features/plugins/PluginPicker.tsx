import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import * as Checkbox from "@radix-ui/react-checkbox";
import * as Label from "@radix-ui/react-label";
import { ApiError } from "../../api";
import { acquireCmsForJob, acquireForJob, listSources, type AcquisitionResult } from "../logs/logsApi";
import TimeFramePicker from "../logs/TimeFramePicker";
import { defaultTimeFrame, resolveTimeFrame, toApi } from "../logs/timeframe";
import { getJobConfiguration, listPlugins, startJob, uploadPcap, type Job, type JobConfiguration, type Plugin } from "./api";
import { RtiOptions, TsharkOptions } from "./ToolOptions";
import { buildOptions, defaultChoices, validateChoices, validateTargetIp } from "./options";
import { useJobs } from "../jobs/JobsContext";
import DeviceFinder from "../devices/DeviceFinder";
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
  onStarted: (job: Job, pullLogs?: () => Promise<AcquisitionResult | AcquisitionResult[]>) => void;
  /** A run is in progress: the form stays visible but can't start another. */
  locked: boolean;
}) {
  const uid = useId();
  const [plugins, setPlugins] = useState<Plugin[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [skipped, setSkipped] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [choices, setChoices] = useState(defaultChoices);
  const [defaults, setDefaults] = useState<JobConfiguration | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [namespace, setNamespace] = useState("");
  const [cpu, setCpu] = useState("");
  const [memory, setMemory] = useState("");
  const [output, setOutput] = useState("");
  const { targetIp, setTargetIp } = useJobs();   // kept when the engineer leaves this page
  const initialTarget = useRef(targetIp);
  const [logsOn, setLogsOn] = useState(true);
  const [logsReason, setLogsReason] = useState<string | null>(null); // why logs can't be collected here
  const [cmsCategory, setCmsCategory] = useState<string | null>(null);
  const [cmsOn, setCmsOn] = useState(false);
  const [frame, setFrame] = useState(defaultTimeFrame);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadName, setUploadName] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const feedback = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getJobConfiguration().then((config) => {
      setDefaults(config);
      setNamespace(config.namespace);
      setCpu(String(config.cpu_limit_pct));
      setMemory(String(config.mem_limit_mb));
      setOutput(String(config.max_output_mb));
      setChoices((current) => ({ ...current,
        tshark: { ...current.tshark, durationS: String(config.tshark.duration_s), interfaceName: config.tshark.interface,
          preset: config.tshark.filter_preset as typeof current.tshark.preset },
        rti: { domain: config.rti.domain, durationS: String(config.rti.duration_s) },
        netshoot: { ports: config.netshoot.ports.join(", "), dnsNames: config.netshoot.dns_names.join(", "),
          extraTargets: config.netshoot.extra_targets.join(", ") },
      }));
    }).catch((e) => setConfigError(messageFor(e)));
    listPlugins()
      .then((r) => { setPlugins(r.plugins.filter((p) => !HIDDEN.has(p.name))); setSkipped(r.load_errors); })
      .catch((e) => setLoadError(messageFor(e)));
    listSources()
      .then((s) => {
        const src = s.find((x) => x.id === LOG_SOURCE);
        if (!src?.configured) { setLogsReason(src?.reason || "The log export API isn't set up on this server."); setLogsOn(false); }
        const category = src?.options?.cms_event_category?.[0];
        setCmsCategory(category ?? null);
      })
      .catch(() => { setLogsReason("Couldn't check the log sources."); setLogsOn(false); });
  }, []);

  // The start button is at the bottom of a long form: bring a failure into view.
  useEffect(() => {
    if (startError) feedback.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [startError]);

  const ipError = validateTargetIp(targetIp);
  const limitError = !!defaults && (!defaults.allowed_namespaces.includes(namespace) ||
    ![[cpu, 100], [memory, 65536], [output, 65536]].every(([value, max]) =>
      /^\d+$/.test(String(value)) && Number(value) >= 1 && Number(value) <= Number(max)));
  const overridden = defaults ? [
    ...(targetIp !== initialTarget.current ? ["target_ip"] : []),
    ...(namespace !== defaults.namespace ? ["namespace"] : []),
    ...(Number(cpu) !== defaults.cpu_limit_pct ? ["cpu_limit_pct"] : []),
    ...(Number(memory) !== defaults.mem_limit_mb ? ["mem_limit_mb"] : []),
    ...(Number(output) !== defaults.max_output_mb ? ["max_output_mb"] : []),
    ...(selected.has("tshark") && Number(choices.tshark.durationS) !== defaults.tshark.duration_s ? ["tshark.duration_s"] : []),
    ...(selected.has("tshark") && choices.tshark.interfaceName !== defaults.tshark.interface ? ["tshark.interface"] : []),
    ...(selected.has("tshark") && choices.tshark.preset !== defaults.tshark.filter_preset ? ["tshark.filter_preset"] : []),
    ...(selected.has("rti") && choices.rti.domain !== defaults.rti.domain ? ["rti.domain"] : []),
    ...(selected.has("rti") && Number(choices.rti.durationS) !== defaults.rti.duration_s ? ["rti.duration_s"] : []),
    ...(selected.has("netshoot") && choices.netshoot.ports !== defaults.netshoot.ports.join(", ") ? ["netshoot.ports"] : []),
    ...(selected.has("netshoot") && choices.netshoot.dnsNames !== defaults.netshoot.dns_names.join(", ") ? ["netshoot.dns_names"] : []),
    ...(selected.has("netshoot") && choices.netshoot.extraTargets !== defaults.netshoot.extra_targets.join(", ") ? ["netshoot.extra_targets"] : []),
  ] : [];
  const fieldErrors = useMemo(() => validateChoices(selected, choices), [selected, choices]);
  const window = useMemo(() => resolveTimeFrame(frame), [frame]);
  const nothingToDo = selected.size === 0 && !logsOn;
  const valid = !ipError && !limitError && !nothingToDo && Object.keys(fieldErrors).length === 0 && !uploading && (!logsOn || window.ok);

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
      const configuration = defaults ? {
        namespace, cpu_limit_pct: Number(cpu), mem_limit_mb: Number(memory), max_output_mb: Number(output),
        overridden_fields: overridden,
      } : undefined;
      const job = await startJob(targetIp.trim(), [...selected], buildOptions(selected, choices), configuration);
      const w = logsOn && window.ok ? toApi(window) : null;
      onStarted(job, w ? () => {
        const pulls = [acquireForJob(LOG_SOURCE, job.id, w.since, w.until)];
        if (cmsOn && cmsCategory) pulls.push(acquireCmsForJob(job.id, w.since, w.until));
        return Promise.all(pulls);
      } : undefined);
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
            : <div className="hint" id={`${uid}-ip-hint`}>For example 192.168.0.3, or pick a device below.</div>}
        </div>
        <DeviceFinder selectedIp={targetIp} onUse={setTargetIp} />
        {overridden.includes("target_ip") && <span className="override-badge">Manually changed</span>}
      </section>

      {configError && <div className="alert warn" role="status"><strong>Run configuration unavailable</strong>
        {configError} Default settings will be used for this run.</div>}
      {defaults && <section aria-label="Run configuration">
        <h2>Review run configuration</h2>
        <p className="muted">Generated values can be changed for this run. Changed fields are marked below.</p>
        <div className="field">
          <Label.Root htmlFor={`${uid}-namespace`}>Diagnostic pod namespace</Label.Root>
          <select id={`${uid}-namespace`} value={namespace} onChange={(e) => setNamespace(e.target.value)}>
            {defaults?.allowed_namespaces.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          {overridden.includes("namespace") && <span className="override-badge">Manually changed</span>}
        </div>
        {([
          ["CPU limit (%)", cpu, setCpu, "cpu_limit_pct"],
          ["Memory limit (MB)", memory, setMemory, "mem_limit_mb"],
          ["Output limit (MB)", output, setOutput, "max_output_mb"],
        ] as const).map(([label, value, setter, key]) => (
          <div className="field" key={key}>
            <Label.Root htmlFor={`${uid}-${key}`}>{label}</Label.Root>
            <input id={`${uid}-${key}`} type="number" min="1" max={key === "cpu_limit_pct" ? 100 : 65536}
              value={value} onChange={(e) => setter(e.target.value)} aria-invalid={submitted && limitError} />
            {overridden.includes(key) && <span className="override-badge">Manually changed</span>}
          </div>
        ))}
        {submitted && limitError && <div className="error-text" role="alert">Choose an enabled namespace and valid positive limits.</div>}
        {overridden.length > 0 && <p className="muted" role="status">Overrides: {overridden.join(", ")}</p>}
      </section>}

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
        {logsOn && <div style={{ marginLeft: 42, marginTop: 10 }}>
          <TimeFramePicker value={frame} onChange={setFrame} legend="Time frame" />
          {cmsCategory ? <div className="plugin-head" style={{ marginTop: 14 }}>
            <Checkbox.Root id={`${uid}-cms-on`} className="check" checked={cmsOn} onCheckedChange={(v) => setCmsOn(v === true)}>
              <Checkbox.Indicator><Check /></Checkbox.Indicator>
            </Checkbox.Root>
            <Label.Root htmlFor={`${uid}-cms-on`} className="plugin-label">
              <span className="plugin-name">Also collect CMS logs</span>
              <span className="muted">Uses GE-confirmed Axone event category {cmsCategory}; CMS rows use the same time range and PHI/PII scan.</span>
            </Label.Root>
          </div> : <p className="muted" style={{ marginTop: 14 }}>CMS logs are unavailable until GE confirms the Axone CMS event-category code.</p>}
        </div>}
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
                {on && p.name === "netshoot" && <div className="plugin-options">
                  {([
                    ["Ports", "ports", "ports"], ["DNS names", "dnsNames", "dnsNames"],
                    ["Additional targets", "extraTargets", "extraTargets"],
                  ] as const).map(([label, key, errorKey]) => <div className="field" key={key}>
                    <Label.Root htmlFor={`${uid}-n-${key}`}>{label} (comma separated)</Label.Root>
                    <input id={`${uid}-n-${key}`} value={choices.netshoot[key]}
                      onChange={(e) => setChoices((c) => ({ ...c, netshoot: { ...c.netshoot, [key]: e.target.value } }))}
                      aria-invalid={!!fieldErrors[`netshoot.${errorKey}`]} />
                    {fieldErrors[`netshoot.${errorKey}`] && <div className="error-text" role="alert">{fieldErrors[`netshoot.${errorKey}`]}</div>}
                  </div>)}
                </div>}
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
