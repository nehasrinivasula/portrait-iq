import * as Select from "@radix-ui/react-select";

// The app's single-choice dropdown (Radix Select): keyboard and screen-reader
// support built in, styled with the theme instead of the browser's default.

const ANY = "__any__";   // Radix reserves "" for "nothing chosen"

export interface DropdownOption { value: string; label?: string; count?: number }

interface Props {
  id: string;
  label: string;
  value: string;                 // "" means the "any" choice
  onChange: (v: string) => void;
  options: DropdownOption[];
  anyLabel: string;              // e.g. "Any device"
  description?: string;          // what this filter means, shown at the top of the list
  emptyText?: string;            // shown when there are no options
  disabled?: boolean;
}

export default function Dropdown({ id, label, value, onChange, options, anyLabel, description, emptyText, disabled }: Props) {
  const current = value === "" ? ANY : value;
  // A value typed elsewhere (e.g. from a link) still shows, even if it is not in this page of options.
  const all = value && !options.some((o) => o.value === value) ? [{ value }, ...options] : options;
  return (
    <div className="field dropdown-field">
      <label id={`${id}-label`} htmlFor={id}>{label}</label>
      <Select.Root value={current} onValueChange={(v) => onChange(v === ANY ? "" : v)} disabled={disabled}>
        <Select.Trigger id={id} className="dropdown-trigger" aria-labelledby={`${id}-label`}>
          <Select.Value placeholder={anyLabel} />
          <Select.Icon className="dropdown-icon" aria-hidden="true">▾</Select.Icon>
        </Select.Trigger>
        <Select.Portal>
          <Select.Content className="dropdown-content" position="popper" sideOffset={4}>
            <Select.ScrollUpButton className="dropdown-scroll" aria-hidden="true">▴</Select.ScrollUpButton>
            <Select.Viewport className="dropdown-viewport">
              {description && <Select.Group><Select.Label className="dropdown-desc">{description}</Select.Label></Select.Group>}
              <Select.Item value={ANY} className="dropdown-item">
                <Select.ItemText>{anyLabel}</Select.ItemText>
              </Select.Item>
              {all.length === 0 && emptyText && <p className="dropdown-empty">{emptyText}</p>}
              {all.map((o) => (
                <Select.Item key={o.value} value={o.value} className="dropdown-item">
                  <Select.ItemText>{o.label ?? o.value}</Select.ItemText>
                  {o.count !== undefined && <span className="dropdown-count">{o.count.toLocaleString()}</span>}
                  <Select.ItemIndicator className="dropdown-check" aria-hidden="true">✓</Select.ItemIndicator>
                </Select.Item>
              ))}
            </Select.Viewport>
            <Select.ScrollDownButton className="dropdown-scroll" aria-hidden="true">▾</Select.ScrollDownButton>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
    </div>
  );
}
