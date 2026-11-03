import { useId, useMemo } from "react";
import * as Label from "@radix-ui/react-label";
import * as RadioGroup from "@radix-ui/react-radio-group";
import { PRESETS, resolveTimeFrame, toLocalInput, type PresetId, type TimeFrameChoice } from "./timeframe";

const SHORT: Record<string, string> = { "1h": "1 hour", "6h": "6 hours", "24h": "24 hours", "7d": "7 days" };
const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export default function TimeFramePicker({ value, onChange, now = new Date(), allowAny = false, limitDays = 30, legend = "Time frame", compact = false }: {
  value: TimeFrameChoice; onChange: (v: TimeFrameChoice) => void; now?: Date;
  /** Offer "Any time" (filtering stored logs). */
  allowAny?: boolean;
  /** How far back a custom range may start; null for no limit. */
  limitDays?: number | null;
  legend?: string;
  /** Hide the resolved-window line and the hint (tight toolbars). */
  compact?: boolean;
}) {
  const uid = useId();
  const resolved = useMemo(() => resolveTimeFrame(value, now, limitDays), [value, now, limitDays]);
  const custom = value.preset === "custom";
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const max = toLocalInput(now);
  const min = limitDays === null ? undefined : toLocalInput(new Date(now.getTime() - limitDays * 864e5));
  const err = resolved.ok ? {} : resolved.errors;

  return (
    <fieldset className="tf" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend style={{ fontWeight: 600, marginBottom: 8 }}>{legend}</legend>
      <RadioGroup.Root className="segmented" aria-label={legend} value={value.preset}
        onValueChange={(p) => onChange({ ...value, preset: p as PresetId })}>
        {allowAny && <RadioGroup.Item value="any" className="seg"><span>Any time</span></RadioGroup.Item>}
        {PRESETS.map((p) => <RadioGroup.Item key={p.id} value={p.id} className="seg"><span>{compact ? SHORT[p.id] : p.label}</span></RadioGroup.Item>)}
        <RadioGroup.Item value="custom" className="seg"><span>{compact ? "Custom" : "Custom range"}</span></RadioGroup.Item>
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

      {!compact && resolved.ok && value.preset !== "any" && (
        <p className="tf-summary" style={{ marginTop: 14 }} role="status">
          {fmt.format(resolved.since)} to {fmt.format(resolved.until)} <span className="muted">({tz}; sent as UTC)</span>
        </p>
      )}
      {!compact && limitDays !== null && <p className="hint">Logs can be pulled for up to the last {limitDays} days.</p>}
    </fieldset>
  );
}
