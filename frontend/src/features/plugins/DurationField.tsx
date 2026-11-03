import * as Label from "@radix-ui/react-label";
import * as RadioGroup from "@radix-ui/react-radio-group";
import { DURATION_PRESETS, type DurationLimits } from "./options";

// Quick lengths plus a number box for anything in between.
export default function DurationField({ id, label, value, onChange, limits, error, hint }: {
  id: string; label: string; value: string; onChange: (v: string) => void; limits: DurationLimits; error?: string; hint?: string;
}) {
  // Only the quick lengths this tool accepts.
  const presets = DURATION_PRESETS.filter((p) => p.s >= limits.min && p.s <= limits.max);
  const preset = presets.find((p) => String(p.s) === value.trim())?.s;
  return (
    <div className="field">
      <Label.Root htmlFor={id}>{label}</Label.Root>
      <RadioGroup.Root className="segmented" aria-label={`${label}, quick choices`} value={preset ? String(preset) : ""}
        onValueChange={onChange}>
        {presets.map((p) => (
          <RadioGroup.Item key={p.s} value={String(p.s)} className="seg"><span>{p.label}</span></RadioGroup.Item>
        ))}
      </RadioGroup.Root>
      <div className="inline-field">
        <input id={id} inputMode="numeric" value={value} aria-invalid={!!error} aria-describedby={error ? `${id}-err` : `${id}-hint`}
          onChange={(e) => onChange(e.target.value)} />
        <span className="muted">seconds ({limits.min} to {limits.max})</span>
      </div>
      {error ? <div className="error-text" id={`${id}-err`} role="alert">{error}</div>
        : hint && <div className="hint" id={`${id}-hint`}>{hint}</div>}
    </div>
  );
}
