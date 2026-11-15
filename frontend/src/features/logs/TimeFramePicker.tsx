import { useId, useMemo } from "react";
import * as Label from "@radix-ui/react-label";
import { PRESETS, resolveTimeFrame, toLocalInput, type PresetId, type TimeFrameChoice } from "./timeframe";

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
    <fieldset className="tf" data-compact={compact || undefined} style={{ border: 0, padding: 0, margin: 0 }}>
      <legend style={{ fontWeight: 600, marginBottom: 8 }}>{legend}</legend>
      <div className="tf-select">
        <Label.Root htmlFor={`${uid}-preset`}>Choose a time frame</Label.Root>
        <select id={`${uid}-preset`} value={value.preset}
          onChange={(e) => onChange({ ...value, preset: e.target.value as PresetId })}>
          {allowAny && <option value="any">Any time</option>}
          {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          <option value="custom">Custom range…</option>
        </select>
      </div>

      {custom && (
        <div className="tf-calendar" style={{ marginTop: 14 }}>
          <p className="tf-calendar-title">Calendar range</p>
          <p className="hint">Choose local dates and times. Portrait IQ converts the range to UTC for the log service.</p>
          <div className="tf-grid">
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
