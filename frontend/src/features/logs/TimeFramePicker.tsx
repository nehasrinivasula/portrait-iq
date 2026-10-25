import { useId, useMemo } from "react";
import * as Label from "@radix-ui/react-label";
import * as RadioGroup from "@radix-ui/react-radio-group";
import { PRESETS, resolveTimeFrame, toLocalInput, type PresetId, type TimeFrameChoice } from "./timeframe";

const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export default function TimeFramePicker({ value, onChange, now = new Date() }: {
  value: TimeFrameChoice; onChange: (v: TimeFrameChoice) => void; now?: Date;
}) {
  const uid = useId();
  const resolved = useMemo(() => resolveTimeFrame(value, now), [value, now]);
  const custom = value.preset === "custom";
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const max = toLocalInput(now);
  const min = toLocalInput(new Date(now.getTime() - 30 * 864e5));
  const err = resolved.ok ? {} : resolved.errors;

  return (
    <fieldset className="tf" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend style={{ fontWeight: 600, marginBottom: 8 }}>Time frame</legend>
      <RadioGroup.Root className="segmented" aria-label="Time frame" value={value.preset}
        onValueChange={(p) => onChange({ ...value, preset: p as PresetId })}>
        {PRESETS.map((p) => <RadioGroup.Item key={p.id} value={p.id} className="seg"><span>{p.label}</span></RadioGroup.Item>)}
        <RadioGroup.Item value="custom" className="seg"><span>Custom range</span></RadioGroup.Item>
      </RadioGroup.Root>

      {custom && (
        <div className="tf-grid" style={{ marginTop: 14 }}>
          {([["since", "From", value.customSince], ["until", "To", value.customUntil]] as const).map(([k, label, v]) => (
            <div className="field" key={k}>
              <Label.Root htmlFor={`${uid}-${k}`}>{label}</Label.Root>
              <input id={`${uid}-${k}`} type="datetime-local" value={v} min={min} max={max}
                aria-invalid={!!err[k]} aria-describedby={err[k] ? `${uid}-${k}-err` : undefined}
                onChange={(e) => onChange({ ...value, [k === "since" ? "customSince" : "customUntil"]: e.target.value })} />
              {err[k] && <div className="error-text" id={`${uid}-${k}-err`} role="alert">{err[k]}</div>}
            </div>
          ))}
        </div>
      )}

      {resolved.ok && (
        <p className="tf-summary" style={{ marginTop: 14 }} role="status">
          {fmt.format(resolved.since)} to {fmt.format(resolved.until)} <span className="muted">({tz}; sent as UTC)</span>
        </p>
      )}
      <p className="hint">Logs can be pulled for up to the last 30 days.</p>
    </fieldset>
  );
}
