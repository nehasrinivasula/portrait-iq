/** UI state mirrors the supported SCRUM-53 /api/logs query filters. */
export type LogFilterState = {
  deviceId: string;
  severity: string;
  category: string;
  /** ISO 8601 UTC timestamp, inclusive. */
  start: string;
  /** ISO 8601 UTC timestamp, exclusive. */
  end: string;
};

export type ActiveFilter = {
  key: keyof LogFilterState;
  label: string;
  value: string;
};

export const EMPTY_LOG_FILTERS: LogFilterState = {
  deviceId: '',
  severity: '',
  category: '',
  start: '',
  end: '',
};

const FILTER_LABELS: Record<keyof LogFilterState, string> = {
  deviceId: 'Device ID',
  severity: 'Severity',
  category: 'Category',
  start: 'Start time',
  end: 'End time',
};

function readableUtc(iso: string) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : `${date.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' })} UTC`;
}

export function activeFilters(filters: LogFilterState): ActiveFilter[] {
  return (Object.keys(FILTER_LABELS) as Array<keyof LogFilterState>)
    .filter((key) => Boolean(filters[key]))
    .map((key) => ({
      key,
      label: FILTER_LABELS[key],
      value: key === 'start' || key === 'end' ? readableUtc(filters[key]) : filters[key],
    }));
}

/**
 * P1 only defines this contract; SCRUM-53 owns the fetch to GET /api/logs.
 * Empty UI fields are omitted so they do not become accidental filters.
 */
export function toLogApiFilters(filters: LogFilterState) {
  const query = new URLSearchParams();
  if (filters.deviceId) query.set('device_id', filters.deviceId);
  if (filters.severity) query.set('severity', filters.severity);
  if (filters.category) query.set('category', filters.category);
  if (filters.start) query.set('start', filters.start);
  if (filters.end) query.set('end', filters.end);
  return query;
}

/** Converts a local datetime picker value to the timezone-aware UTC API form. */
export function localDateTimeToUtc(value: string) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toISOString();
}

/** Converts a stored UTC value to the local form required by datetime-local. */
export function utcToLocalDateTime(value: string) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (number: number) => String(number).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
