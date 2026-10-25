import { apiFetch } from '../../api';

// Shapes returned by the backend's /api/sources routes (backend/app/schemas.py).

export interface Source {
  id: string;
  display_name: string;
  needs_network: boolean;
  configured: boolean;
  /** Why the source can't be used yet, when `configured` is false. */
  reason: string;
}

export interface SourceStatus {
  ok: boolean;
  reason: string;
}

export type FileStatus =
  | 'ingested'
  | 'acquired'
  | 'already_acquired'
  | 'duplicate'
  | 'unrecognized'
  | 'over_budget'
  | 'failed';

export interface FileOutcome {
  name: string;
  status: FileStatus;
  size: number | null;
  events: number | null;
  error: string | null;
}

export interface Acquisition {
  run_id: string;
  source: string;
  new_files: number;
  already_acquired: number;
  failed: number;
  over_budget: number;
  bytes_staged: number;
  files: FileOutcome[];
  /** Extra explanations from the backend (e.g. an end time moved back). */
  notes?: string[];
}

// --- API ----------------------------------------------------------------------

export const listSources = () => apiFetch<Source[]>('/api/sources');

export const testSource = (id: string) =>
  apiFetch<SourceStatus>(`/api/sources/${encodeURIComponent(id)}/test`, { method: 'POST' });

/** Upload files or ZIPs; the backend imports them like any other local file
 *  (same duplicate checks, ZIP safety and audit log). */
export function uploadLogs(files: File[]): Promise<Acquisition> {
  const form = new FormData();
  for (const f of files) form.append('files', f, f.name);
  return apiFetch<Acquisition>('/api/sources/local/upload', { method: 'POST', body: form });
}

// --- wording ------------------------------------------------------------------

export const ACCEPTED = ['.csv', '.zip', '.log', '.txt'];

export const isAccepted = (name: string) =>
  ACCEPTED.some((ext) => name.toLowerCase().endsWith(ext));

export const plural = (n: number, word: string) =>
  `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

export function formatBytes(bytes: number | null): string {
  if (bytes == null) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
}

/** How each file is shown: plain words plus a tone for its coloured border. */
export function statusText(f: FileOutcome): { text: string; tone: 'ok' | 'skip' | 'warn' | 'bad' } {
  switch (f.status) {
    case 'ingested':
      return { text: `Imported, ${plural(f.events ?? 0, 'log row')}`, tone: 'ok' };
    case 'acquired':
      return { text: 'Imported', tone: 'ok' };
    case 'already_acquired':
      return { text: 'Already imported earlier, skipped', tone: 'skip' };
    case 'duplicate':
      return { text: 'Same content as a file already imported, skipped', tone: 'skip' };
    case 'unrecognized':
      return { text: "Brought in, but this log format isn't recognised yet", tone: 'warn' };
    case 'over_budget':
      return { text: 'Skipped: the size limit for one import was reached', tone: 'warn' };
    case 'failed':
      return { text: `Failed: ${f.error ?? 'unknown reason'}`, tone: 'bad' };
  }
}

export const rowsIn = (r: Acquisition) => r.files.reduce((sum, f) => sum + (f.events ?? 0), 0);

/** One sentence for the result banner. */
export function summarize(r: Acquisition): { tone: 'ok' | 'bad' | 'neutral'; headline: string } {
  if (r.files.length === 0) return { tone: 'neutral', headline: 'No log files were found in what you chose.' };
  if (r.new_files === 0 && r.failed === 0 && r.over_budget === 0) {
    return {
      tone: 'neutral',
      headline: 'Nothing new. These files were already imported, so nothing was downloaded twice.',
    };
  }
  const parts: string[] = [];
  if (r.new_files) parts.push(`Imported ${plural(r.new_files, 'new file')}, ${plural(rowsIn(r), 'log row')}.`);
  const unknown = r.files.filter((f) => f.status === 'unrecognized').length;
  if (unknown) parts.push(`${unknown.toLocaleString()} not in a recognised log format.`);
  if (r.already_acquired) parts.push(`${r.already_acquired.toLocaleString()} already imported, skipped.`);
  if (r.failed) parts.push(`${plural(r.failed, 'file')} failed.`);
  if (r.over_budget) parts.push(`${r.over_budget.toLocaleString()} skipped for size.`);
  return { tone: r.failed ? 'bad' : 'ok', headline: parts.join(' ') };
}
