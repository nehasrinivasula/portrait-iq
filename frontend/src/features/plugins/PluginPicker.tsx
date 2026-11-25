import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import * as Checkbox from "@radix-ui/react-checkbox";
import * as Label from "@radix-ui/react-label";
import { ApiError } from "../../api";
import type { TaskProgress } from "../tasks/tasksApi";
import { acquireCmsForJob, acquireForJob, listSources, type AcquisitionResult } from "../logs/logsApi";
import TimeFramePicker from "../logs/TimeFramePicker";
import { defaultTimeFrame, resolveTimeFrame, toApi } from "../logs/timeframe";
import { getJobConfiguration, listPlugins, startJob, uploadPcap, type Job, type JobConfiguration, type Plugin } from "./api";
import { PrometheusTsdbOptions, RtiOptions, TsharkOptions } from "./ToolOptions";
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
  onStarted: (job: Job, pullLogs?: (onProgress: TaskProgress) => Promise<AcquisitionResult | AcquisitionResult[]>) => void;
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
  const [checkInterval, setCheckInterval] = useState("1");
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
  const [touched, setTouched] = useState<Set<string>>(new Set());   // fields left once: their errors show from then on
  const [limitsOpen, setLimitsOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);                         // bumps on each Start press, to focus the first bad field
  const form = useRef<HTMLFormElement>(null);
  const leave = (key: string) => setTouched((t) => (t.has(key) ? t : new Set(t).add(key)));
  const shows = (key: string) => submitted || touched.has(key);

  useEffect(() => {
    getJobConfiguration().then((config) => {
      setDefaults(config);
      setNamespace(config.namespace);
      setCpu(String(config.cpu_limit_pct));
      setMemory(String(config.mem_limit_mb));
      setOutput(String(config.max_output_mb));
      setCheckInterval(String(config.netshoot.check_interval_seconds));
      setChoices((current) => ({ ...current,
        tshark: { ...current.tshark, durationS: String(config.tshark.duration_s), interfaceName: config.tshark.interface,
          preset: config.tshark.filter_preset as typeof current.tshark.preset },
        rti: { domain: config.rti.domain, durationS: String(config.rti.duration_s) },
        netshoot: { ports: config.netshoot.ports.join(", "), dnsNames: config.netshoot.dns_names.join(", "),
          extraTargets: config.netshoot.extra_targets.join(", ") },
        prometheus_tsdb: { scope: config.prometheus_tsdb.scope as typeof current.prometheus_tsdb.scope },
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

  // Start pressed with problems: put the cursor in the first field that needs fixing.
  useEffect(() => {
    if (attempt) form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [attempt]);

  const ipError = validateTargetIp(targetIp);
  const LIMITS = [
    { key: "cpu_limit_pct", label: "CPU limit", unit: "%", value: cpu, set: setCpu, max: 100 },
    { key: "mem_limit_mb", label: "Memory limit", unit: "MB", value: memory, set: setMemory, max: 65536 },
    { key: "max_output_mb", label: "Output limit", unit: "MB", value: output, set: setOutput, max: 65536 },
    { key: "netshoot.check_interval_seconds", label: "Network check interval", unit: "seconds", value: checkInterval, set: setCheckInterval, max: 60 },
  ].filter(({ key }) => key !== "netshoot.check_interval_seconds" || selected.has("netshoot"));
  const limitErrors: Record<string, string> = Object.fromEntries(LIMITS.flatMap(({ key, value, max }) =>
    /^\d+$/.test(value) && Number(value) >= 1 && Number(value) <= max ? [] : [[key, `Enter a whole number from 1 to ${max.toLocaleString()}.`]]));
  const limitError = !!defaults && (!defaults.allowed_namespaces.includes(namespace) || Object.keys(limitErrors).length > 0);
  const overridden = defaults ? [
    ...(targetIp !== initialTarget.current ? ["target_ip"] : []),
    ...(namespace !== defaults.namespace ? ["namespace"] : []),
    ...(Number(cpu) !== defaults.cpu_limit_pct ? ["cpu_limit_pct"] : []),
    ...(Number(memory) !== defaults.mem_limit_mb ? ["mem_limit_mb"] : []),
    ...(Number(output) !== defaults.max_output_mb ? ["max_output_mb"] : []),
    ...(selected.has("netshoot") && Number(checkInterval) !== defaults.netshoot.check_interval_seconds ? ["netshoot.check_interval_seconds"] : []),
    ...(selected.has("tshark") && Number(choices.tshark.durationS) !== defaults.tshark.duration_s ? ["tshark.duration_s"] : []),
    ...(selected.has("tshark") && choices.tshark.interfaceName !== defaults.tshark.interface ? ["tshark.interface"] : []),
    ...(selected.has("tshark") && choices.tshark.preset !== defaults.tshark.filter_preset ? ["tshark.filter_preset"] : []),
    ...(selected.has("rti") && choices.rti.domain !== defaults.rti.domain ? ["rti.domain"] : []),
    ...(selected.has("rti") && Number(choices.rti.durationS) !== defaults.rti.duration_s ? ["rti.duration_s"] : []),
    ...(selected.has("prometheus_tsdb") && choices.prometheus_tsdb.scope !== defaults.prometheus_tsdb.scope ? ["prometheus_tsdb.scope"] : []),
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
    if (!valid || locked) {
      if (limitError) setLimitsOpen(true);
      setAttempt((n) => n + 1);
      return;
    }
    setBusy(true);
    try {
      const configuration = defaults ? {
        namespace, cpu_limit_pct: Number(cpu), mem_limit_mb: Number(memory), max_output_mb: Number(output),
        overridden_fields: overridden,
      } : undefined;
      const options = buildOptions(selected, choices);
      if (selected.has("netshoot")) options.netshoot = { ...options.netshoot, check_interval_seconds: Number(checkInterval) };
      const job = await startJob(targetIp.trim(), [...selected], options, configuration);
      const w = logsOn && window.ok ? toApi(window) : null;
      onStarted(job, w ? async (onProgress) => {
        // Keep the existing source pull first. CMS is optional and uses the
        // same bounded window; waiting avoids two heavy pulls competing for
        // the single background worker while retaining real task progress.
        const system = await acquireForJob(LOG_SOURCE, job.id, w.since, w.until, onProgress);
        if (!cmsOn || !cmsCategory) return system;
        const cms = await acquireCmsForJob(job.id, w.since, w.until, onProgress);
        return [system, cms];
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

  const configChanged = overridden.filter((key) => key === "namespace" || LIMITS.some((limit) => limit.key === key)).length;
  const toolNames = (plugins ?? []).filter((p) => selected.has(p.name)).map((p) => p.display_name);
  // One line above Start: what will happen, or the one thing stopping it.
  const problem = locked ? "A run is in progress. You can start another when it finishes."
    : startError ? `Couldn't start the run. ${startError}`
    : submitted && nothingToDo ? "Choose at least one diagnostic, or include logs."
    : submitted && !valid ? "Fix the fields marked in red."
    : null;
  const summary = [
    targetIp.trim() && !ipError ? targetIp.trim() : "No target yet",
    toolNames.length ? toolNames.join(", ") : "No diagnostics",
    logsOn ? "with logs" : null,
  ].filter(Boolean).join(" · ");
  const changed = <span className="override-badge">Changed</span>;

  return (
    <form className="picker" onSubmit={onSubmit} noValidate ref={form}>
      <section aria-labelledby={`${uid}-target`}>
        <h2 id={`${uid}-target`}>Where to run it</h2>
        <div className="field">
          <Label.Root htmlFor={`${uid}-ip`}>Portrait Mobile server IP address {overridden.includes("target_ip") && changed}</Label.Root>
          <input id={`${uid}-ip`} inputMode="decimal" autoComplete="off" value={targetIp} aria-invalid={shows("ip") && !!ipError}
            aria-describedby={shows("ip") && ipError ? `${uid}-ip-err` : `${uid}-ip-hint`}
            onChange={(e) => setTargetIp(e.target.value)} onBlur={() => targetIp && leave("ip")} />
          {shows("ip") && ipError ? <div className="error-text" id={`${uid}-ip-err`} role="alert">{ipError}</div>
            : <div className="hint" id={`${uid}-ip-hint`}>For example 192.168.0.3, or pick a device below.</div>}
        </div>
        <DeviceFinder selectedIp={targetIp} onUse={setTargetIp} />
      </section>

      <section aria-labelledby={`${uid}-tools`}>
        <div className="section-head">
          <h2 id={`${uid}-tools`}>What to run</h2>
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
                {on && p.name === "prometheus_tsdb" && <PrometheusTsdbOptions uid={uid} choices={choices} onChoices={setChoices} />}
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
      </section>

      <section aria-labelledby={`${uid}-logs`}>
        <h2 id={`${uid}-logs`}>Logs to include</h2>
        <div className="plugin-head">
          <Checkbox.Root id={`${uid}-logs-on`} className="check" checked={logsOn} disabled={!!logsReason}
            onCheckedChange={(v) => setLogsOn(v === true)}>
            <Checkbox.Indicator><Check /></Checkbox.Indicator>
          </Checkbox.Root>
          <Label.Root htmlFor={`${uid}-logs-on`} className="plugin-label" data-disabled={logsReason ? "" : undefined}>
            <span className="plugin-name">Collect system logs with this run</span>
            <span className="muted">{logsReason ?? "Pulled from the log export API and added to the log table automatically."}</span>
          </Label.Root>
        </div>
        {logsOn && <div className="plugin-options">
          <TimeFramePicker value={frame} onChange={setFrame} legend="Time frame" />
          {cmsCategory ? <div className="plugin-head cms-option">
            <Checkbox.Root id={`${uid}-cms-on`} className="check" checked={cmsOn} onCheckedChange={(v) => setCmsOn(v === true)}>
              <Checkbox.Indicator><Check /></Checkbox.Indicator>
            </Checkbox.Root>
            <Label.Root htmlFor={`${uid}-cms-on`} className="plugin-label">
              <span className="plugin-name">Also collect CMS logs</span>
              <span className="muted">Uses GE-confirmed Axone event category {cmsCategory}; CMS rows use the same time range and PHI/PII scan.</span>
            </Label.Root>
          </div> : <p className="muted cms-option">CMS logs are unavailable until GE confirms the Axone CMS event-category code.</p>}
        </div>}
      </section>

      {configError && <div className="alert warn" role="status"><strong>Run configuration unavailable</strong>
        {configError} Default settings will be used for this run.</div>}
      {/* Generated defaults are safe, so the overrides (D-7) stay one click away instead of in the path. */}
      {defaults && (
        <details className="strip limits" open={limitsOpen} onToggle={(e) => setLimitsOpen(e.currentTarget.open)}>
          <summary>
            <span className="strip-title">Limits and namespace</span>
            <span className={configChanged ? "override-badge" : "muted"}>{configChanged ? `${configChanged} changed` : "Defaults"}</span>
          </summary>
          <div className="strip-body">
            <div className="field">
              <Label.Root htmlFor={`${uid}-namespace`}>Diagnostic pod namespace {overridden.includes("namespace") && changed}</Label.Root>
              <select id={`${uid}-namespace`} value={namespace} onChange={(e) => setNamespace(e.target.value)}>
                {defaults.allowed_namespaces.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </div>
            <div className="limit-row">
              {LIMITS.map(({ key, label, unit, value, set, max }) => {
                const err = shows(key) ? limitErrors[key] : undefined;
                return (
                  <div className="field" key={key}>
                    <Label.Root htmlFor={`${uid}-${key}`}>{label} ({unit}) {overridden.includes(key) && changed}</Label.Root>
                    <input id={`${uid}-${key}`} type="number" min="1" max={max} value={value}
                      onChange={(e) => set(e.target.value)} onBlur={() => leave(key)}
                      aria-invalid={!!err} aria-describedby={err ? `${uid}-${key}-err` : undefined} />
                    {err && <div className="error-text" id={`${uid}-${key}-err`} role="alert">{err}</div>}
                  </div>
                );
              })}
            </div>
            {selected.has("netshoot") && <p className="muted">Netshoot waits this long after each check before starting the next one.</p>}
          </div>
        </details>
      )}

      {/* Start stays in view at the bottom of the screen, with what it will do or what is stopping it. */}
      <div className="action-bar">
        <span className={problem ? "text-error action-bar-text" : "action-bar-text"} role={problem ? "alert" : "status"}>{problem ?? summary}</span>
        <button className="btn" type="submit" disabled={busy || uploading || locked}>{label}</button>
      </div>
    </form>
  );
}
