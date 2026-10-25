import * as Label from "@radix-ui/react-label";
import * as RadioGroup from "@radix-ui/react-radio-group";
import { DURATION, DURATION_PRESETS } from "./options";

// Quick lengths plus a number box for anything in between.
export default function DurationField({ id, label, value, onChange, error, hint }: {
  id: string; label: string; value: string; onChange: (v: string) => void; error?: string; hint?: string;
}) {
  const preset = DURATION_PRESETS.find((p) => String(p.s) === value.trim())?.s;
  return (
    <div className="field">
      <Label.Root htmlFor={id}>{label}</Label.Root>
      <RadioGroup.Root className="segmented" aria-label={`${label}, quick choices`} value={preset ? String(preset) : ""}
        onValueChange={onChange}>
        {DURATION_PRESETS.map((p) => (
          <RadioGroup.Item key={p.s} value={String(p.s)} className="seg"><span>{p.label}</span></RadioGroup.Item>
        ))}
      </RadioGroup.Root>
      <div className="inline-field">
        <input id={id} inputMode="numeric" value={value} aria-invalid={!!error} aria-describedby={error ? `${id}-err` : `${id}-hint`}
          onChange={(e) => onChange(e.target.value)} />
        <span className="muted">seconds ({DURATION.min} to {DURATION.max})</span>
      </div>
      {error ? <div className="error-text" id={`${id}-err`} role="alert">{error}</div>
        : hint && <div className="hint" id={`${id}-hint`}>{hint}</div>}
    </div>
  );
}
