import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import * as Checkbox from "@radix-ui/react-checkbox";
import * as Label from "@radix-ui/react-label";
import * as RadioGroup from "@radix-ui/react-radio-group";
import { ApiError } from "../../api";
import { listPlugins, startJob, uploadPcap, type Job, type Plugin } from "./api";
import {
  RTI_DOMAINS, RTI_DURATION, TSHARK_DURATION, buildOptions, defaultChoices, validateChoices, validateTargetIp,
} from "./options";
import "./plugins.css";

const Check = () => (
  <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
    <path d="M3 8.5l3.2 3.2L13 4.8" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/** Reads a message the field engineer can act on from a failed request. */
const messageFor = (err: unknown) =>
  err instanceof ApiError
    ? err.status === 0 ? "Can't reach the server. Check the connection and try again."
      : typeof err.message === "string" ? err.message : "The server rejected the request."
    : "Something went wrong. Try again.";

function Field({ id, label, hint, error, children }: {
  id: string; label: string; hint?: string; error?: string; children: React.ReactNode;
}) {
  return (
    <div className="field">
      <Label.Root htmlFor={id}>{label}</Label.Root>
      {children}
      {hint && !error && <div className="hint" id={`${id}-hint`}>{hint}</div>}
      {error && <div className="error-text" id={`${id}-err`} role="alert">{error}</div>}
    </div>
  );
}

export default function PluginPicker({ onStarted }: { onStarted: (job: Job) => void }) {
  const uid = useId();
  const [plugins, setPlugins] = useState<Plugin[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [skipped, setSkipped] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [choices, setChoices] = useState(defaultChoices);
  const [targetIp, setTargetIp] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadName, setUploadName] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  useEffect(() => {
    listPlugins()
      .then((r) => { setPlugins(r.plugins); setSkipped(r.load_errors); })
      .catch((e) => setLoadError(messageFor(e)));
  }, []);

  const ipError = validateTargetIp(targetIp);
  const fieldErrors = useMemo(() => validateChoices(selected, choices), [selected, choices]);
  const noneSelected = selected.size === 0;
  const valid = !ipError && !noneSelected && Object.keys(fieldErrors).length === 0 && !uploading;

  const toggle = (name: string, on: boolean) =>
    setSelected((s) => { const n = new Set(s); on ? n.add(name) : n.delete(name); return n; });

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
    if (!valid) return;
    setBusy(true);
    try {
      onStarted(await startJob(targetIp.trim(), [...selected], buildOptions(selected, choices)));
    } catch (err) {
      setStartError(messageFor(err));
    } finally {
      setBusy(false);
    }
  }

  const optionsFor = (name: string) => {
    if (name === "tshark") {
      const t = choices.tshark;
      return (
        <div className="plugin-options">
          <RadioGroup.Root
            className="segmented" aria-label="tshark mode" value={t.mode}
            onValueChange={(v) => setChoices((c) => ({ ...c, tshark: { ...c.tshark, mode: v as "run" | "import" } }))}
          >
            <RadioGroup.Item value="run" id={`${uid}-t-run`} className="seg"><span>Capture live</span></RadioGroup.Item>
            <RadioGroup.Item value="import" id={`${uid}-t-imp`} className="seg"><span>Import a capture file</span></RadioGroup.Item>
          </RadioGroup.Root>
          {t.mode === "run" ? (
            <Field id={`${uid}-t-dur`} label="Capture length (seconds)"
              hint={`${TSHARK_DURATION.min} to ${TSHARK_DURATION.max}. Packet headers only; no message contents are kept.`}
              error={fieldErrors["tshark.duration"]}>
              <input id={`${uid}-t-dur`} inputMode="numeric" value={t.durationS}
                aria-invalid={!!fieldErrors["tshark.duration"]}
                onChange={(e) => setChoices((c) => ({ ...c, tshark: { ...c.tshark, durationS: e.target.value } }))} />
            </Field>
          ) : (
            <Field id={`${uid}-t-file`} label="Capture file (.pcap or .pcapng)"
              hint={uploading ? "Uploading…" : uploadName ? `Uploaded: ${uploadName}` : "Up to 500 MB."}
              error={uploadError ?? fieldErrors["tshark.upload"]}>
              <input id={`${uid}-t-file`} type="file" accept=".pcap,.pcapng"
                aria-invalid={!!(uploadError ?? fieldErrors["tshark.upload"])}
                onChange={(e) => onFile(e.target.files?.[0])} />
            </Field>
          )}
        </div>
      );
    }
    if (name === "rti") {
      return (
        <div className="plugin-options">
          <RadioGroup.Root className="segmented" aria-label="DDS domain" value={String(choices.rti.domain)}
            onValueChange={(v) => setChoices((c) => ({ ...c, rti: { ...c.rti, domain: Number(v) } }))}>
            {RTI_DOMAINS.map((d) => (
              <RadioGroup.Item key={d} value={String(d)} className="seg"><span>Domain {d}</span></RadioGroup.Item>
            ))}
          </RadioGroup.Root>
          <Field id={`${uid}-r-dur`} label="Listen time (seconds)"
            hint={`${RTI_DURATION.min} to ${RTI_DURATION.max}. Reports which hosts and topics are active, never message contents.`}
            error={fieldErrors["rti.duration"]}>
            <input id={`${uid}-r-dur`} inputMode="numeric" value={choices.rti.durationS}
              aria-invalid={!!fieldErrors["rti.duration"]}
              onChange={(e) => setChoices((c) => ({ ...c, rti: { ...c.rti, durationS: e.target.value } }))} />
          </Field>
        </div>
      );
    }
    return null;
  };

  return (
    <form className="picker" onSubmit={onSubmit} noValidate>
      <section aria-labelledby={`${uid}-tools`}>
        <h2 id={`${uid}-tools`}>Choose what to run</h2>
        <p className="muted">Pick one or more diagnostics. Each one is removed from the system when it finishes.</p>

        {loadError && <div className="alert error" role="alert"><strong>Couldn't load the diagnostics</strong>{loadError}</div>}
        {!plugins && !loadError && <p className="muted" role="status">Loading diagnostics…</p>}

        <ul className="plugin-list">
          {plugins?.map((p) => {
            const on = selected.has(p.name);
            return (
              <li key={p.name} className={`plugin-card${on ? " on" : ""}`}>
                <div className="plugin-head">
                  <Checkbox.Root id={`${uid}-${p.name}`} className="check" checked={on}
                    onCheckedChange={(v) => toggle(p.name, v === true)}>
                    <Checkbox.Indicator><Check /></Checkbox.Indicator>
                  </Checkbox.Root>
                  <Label.Root htmlFor={`${uid}-${p.name}`} className="plugin-label">
                    <span className="plugin-name">{p.display_name}</span>
                    <span className="muted">{p.description}</span>
                  </Label.Root>
                </div>
                {on && optionsFor(p.name)}
              </li>
            );
          })}
        </ul>

        {Object.entries(skipped).map(([name, why]) => (
          <div key={name} className="alert warn" role="status"><strong>{name} is unavailable</strong>{why}</div>
        ))}
        {submitted && noneSelected && <div className="error-text" role="alert">Choose at least one diagnostic.</div>}
      </section>

      <section aria-labelledby={`${uid}-target`}>
        <h2 id={`${uid}-target`}>Where to run it</h2>
        <Field id={`${uid}-ip`} label="Portrait Mobile server IP address" hint="For example 192.168.0.3."
          error={submitted ? ipError ?? undefined : undefined}>
          <input id={`${uid}-ip`} inputMode="decimal" autoComplete="off" value={targetIp}
            aria-invalid={submitted && !!ipError}
            onChange={(e) => setTargetIp(e.target.value)} />
        </Field>
      </section>

      {startError && <div className="alert error" role="alert"><strong>Couldn't start the run</strong>{startError}</div>}
      <button className="btn" type="submit" disabled={busy || uploading}>
        {busy ? "Starting…" : `Run ${selected.size || ""} diagnostic${selected.size === 1 ? "" : "s"}`.replace("  ", " ")}
      </button>
    </form>
  );
}
