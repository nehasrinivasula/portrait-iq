// Shared validators for free-text fields (requirement D-9). Each returns an
// error message, or null when the value is fine. The backend enforces the
// same rules in backend/app/validation.py; keep the limits in step.

export const MAX_USERNAME = 128;
export const MAX_PASSWORD = 256;
export const MAX_FILTER_VALUE = 200;
export const MAX_ID = 128;
export const MAX_LABEL = 64;
export const MAX_HOST = 253;
export const IMPORT_SUFFIXES = [".csv", ".zip", ".log", ".txt"] as const;

// Control characters (incl. newline and tab) and invisible format characters
// such as zero-width spaces and bidi overrides.
const CONTROL = /[\p{Cc}\p{Cf}]/u;
export const hasControlChars = (v: string): boolean => CONTROL.test(v);

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Length and character checks for one free-text value. Empty is allowed unless `required`. */
export function validateText(
  value: string, label: string, opts: { max: number; required?: boolean; foldWhitespace?: boolean },
): string | null {
  // Search text is pasted from logs, which contain tabs and line breaks; the API treats those as spaces.
  const v = (opts.foldWhitespace ? value.replace(/[\t\n\r]/g, " ") : value).trim();
  if (!v) return opts.required ? `Enter ${label}.` : null;
  if (v.length > opts.max) return `${cap(label)} is limited to ${opts.max} characters (now ${v.length}).`;
  if (hasControlChars(v)) return `${cap(label)} can't contain control or invisible characters.`;
  return null;
}

// No leading zeros ("010.0.0.1" is ambiguous), same as Python's ipaddress.
const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
export const validateTargetIp = (v: string): string | null =>
  !v.trim() ? "Enter the IP address of the Portrait Mobile server."
  : IPV4.test(v.trim()) ? null
  : "Enter a valid IPv4 address, for example 192.168.0.3.";

const HOST_LABEL = /^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$/;
/** An IPv4 address or a DNS host name. */
export function validateHost(v: string): string | null {
  const t = v.trim();
  if (!t) return "Enter a host name or IP address.";
  if (t.length > MAX_HOST) return `Host name is limited to ${MAX_HOST} characters.`;
  if (/^[0-9.]+$/.test(t)) return IPV4.test(t) ? null : "Enter a valid IPv4 address, for example 192.168.0.3.";
  return t.replace(/\.$/, "").split(".").every((p) => HOST_LABEL.test(p))
    ? null
    : "Use letters, digits and hyphens only, with dots between parts (for example lab-pc.local).";
}

export function validatePort(v: string): string | null {
  const t = v.trim();
  return /^\d{1,5}$/.test(t) && Number(t) >= 1 && Number(t) <= 65535 ? null : "Enter a port number between 1 and 65535.";
}

export const validateLabel = (v: string): string | null => validateText(v, "device label", { max: MAX_LABEL });
export const validateUsername = (v: string): string | null => validateText(v, "your username", { max: MAX_USERNAME, required: true });

/** Passwords are never trimmed or character-checked, only required and bounded. */
export const validatePassword = (v: string): string | null =>
  !v ? "Enter your password." : v.length > MAX_PASSWORD ? `Password is limited to ${MAX_PASSWORD} characters.` : null;

export const validateFilterValue = (v: string, label: string): string | null =>
  validateText(v, label, { max: MAX_FILTER_VALUE });

/** The log search box: like a filter value, but tabs and line breaks are fine (they count as spaces). */
export const validateSearchText = (v: string): string | null =>
  validateText(v, "search text", { max: MAX_FILTER_VALUE, foldWhitespace: true });

export const validateIdFilter = (v: string, label: string): string | null => validateText(v, label, { max: MAX_ID });

/** Checks a file chosen for import by name; returns one message per rejected file. */
export function validateImportFiles(names: string[]): string[] {
  const errors: string[] = [];
  for (const name of names) {
    const dot = name.lastIndexOf(".");
    const ext = dot > 0 ? name.slice(dot).toLowerCase() : "";
    if (!(IMPORT_SUFFIXES as readonly string[]).includes(ext))
      errors.push(`"${name}" isn't a supported file type. Use ${IMPORT_SUFFIXES.join(", ")}.`);
    else if (name.length > 255) errors.push(`"${name.slice(0, 40)}…" has a name longer than 255 characters.`);
  }
  return errors;
}
