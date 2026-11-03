import * as Label from "@radix-ui/react-label";
import * as RadioGroup from "@radix-ui/react-radio-group";
import DurationField from "./DurationField";
import { RTI_DOMAINS, RTI_DURATION, TSHARK_DURATION, TSHARK_PRESETS, type Choices, type TsharkChoice } from "./options";

interface Props {
  uid: string;
  choices: Choices;
  onChoices: (c: Choices) => void;
  errors: Record<string, string>;
  upload: { name: string | null; busy: boolean; error: string | null; onFile: (f: File | undefined) => void };
}

// Settings for the tools that have any. Each tool keeps its own limits.
export function TsharkOptions({ uid, choices, onChoices, errors, upload }: Props) {
  const t = choices.tshark;
  const set = (patch: Partial<TsharkChoice>) => onChoices({ ...choices, tshark: { ...t, ...patch } });

  return (
    <div className="plugin-options">
      <RadioGroup.Root className="segmented" aria-label="tshark mode" value={t.mode}
        onValueChange={(v) => set({ mode: v as TsharkChoice["mode"] })}>
        <RadioGroup.Item value="run" className="seg"><span>Capture live</span></RadioGroup.Item>
        <RadioGroup.Item value="import" className="seg"><span>Import a capture file</span></RadioGroup.Item>
      </RadioGroup.Root>

      {t.mode === "run" ? (
        <>
          <DurationField id={`${uid}-t-dur`} label="Capture length" value={t.durationS} onChange={(v) => set({ durationS: v })} limits={TSHARK_DURATION}
            error={errors["tshark.duration"]} hint="Packet headers only; no message contents are kept." />

          <div className="field">
            <span className="filter-label" id={`${uid}-t-filter`}>What to capture</span>
            <RadioGroup.Root className="segmented" aria-labelledby={`${uid}-t-filter`} value={t.filterKind}
              onValueChange={(v) => set({ filterKind: v as TsharkChoice["filterKind"] })}>
              <RadioGroup.Item value="quick" className="seg"><span>Quick</span></RadioGroup.Item>
              <RadioGroup.Item value="manual" className="seg"><span>Manual port</span></RadioGroup.Item>
            </RadioGroup.Root>
          </div>

          {t.filterKind === "quick" ? (
            <RadioGroup.Root className="option-list" aria-label="Quick capture filter" value={t.preset}
              onValueChange={(v) => set({ preset: v as TsharkChoice["preset"] })}>
              {TSHARK_PRESETS.map((p) => (
                <RadioGroup.Item key={p.id} value={p.id} className="seg option">
                  <span>{p.label}</span> <span className="opt-hint">{p.hint}</span>
                </RadioGroup.Item>
              ))}
            </RadioGroup.Root>
          ) : (
            <div className="field">
              <Label.Root htmlFor={`${uid}-t-port`}>Port number</Label.Root>
              <div className="inline-field">
                <input id={`${uid}-t-port`} inputMode="numeric" value={t.port} aria-invalid={!!errors["tshark.port"]}
                  aria-describedby={errors["tshark.port"] ? `${uid}-t-port-err` : undefined}
                  onChange={(e) => set({ port: e.target.value })} style={{ maxWidth: 140 }} />
                <RadioGroup.Root className="segmented" aria-label="Protocol" value={t.proto}
                  onValueChange={(v) => set({ proto: v as TsharkChoice["proto"] })}>
                  <RadioGroup.Item value="any" className="seg"><span>TCP or UDP</span></RadioGroup.Item>
                  <RadioGroup.Item value="tcp" className="seg"><span>TCP</span></RadioGroup.Item>
                  <RadioGroup.Item value="udp" className="seg"><span>UDP</span></RadioGroup.Item>
                </RadioGroup.Root>
              </div>
              {errors["tshark.port"] && <div className="error-text" id={`${uid}-t-port-err`} role="alert">{errors["tshark.port"]}</div>}
            </div>
          )}
        </>
      ) : (
        <div className="field">
          <Label.Root htmlFor={`${uid}-t-file`}>Capture file (.pcap or .pcapng)</Label.Root>
          <input id={`${uid}-t-file`} type="file" accept=".pcap,.pcapng" aria-invalid={!!(upload.error ?? errors["tshark.upload"])}
            onChange={(e) => upload.onFile(e.target.files?.[0])} />
          {upload.error || errors["tshark.upload"]
            ? <div className="error-text" role="alert">{upload.error ?? errors["tshark.upload"]}</div>
            : <div className="hint">{upload.busy ? "Uploading…" : upload.name ? `Uploaded: ${upload.name}` : "Up to 500 MB."}</div>}
        </div>
      )}
    </div>
  );
}

export function RtiOptions({ uid, choices, onChoices, errors }: Omit<Props, "upload">) {
  const r = choices.rti;
  return (
    <div className="plugin-options">
      <div className="field">
        <span className="filter-label" id={`${uid}-r-dom`}>DDS domain</span>
        <RadioGroup.Root className="segmented" aria-labelledby={`${uid}-r-dom`} value={String(r.domain)}
          onValueChange={(v) => onChoices({ ...choices, rti: { ...r, domain: Number(v) } })}>
          {RTI_DOMAINS.map((d) => <RadioGroup.Item key={d} value={String(d)} className="seg"><span>Domain {d}</span></RadioGroup.Item>)}
        </RadioGroup.Root>
      </div>
      <DurationField id={`${uid}-r-dur`} label="Listen time" value={r.durationS}
        onChange={(v) => onChoices({ ...choices, rti: { ...r, durationS: v } })} limits={RTI_DURATION} error={errors["rti.duration"]}
        hint="Reports which hosts and topics are active, never message contents." />
    </div>
  );
}
