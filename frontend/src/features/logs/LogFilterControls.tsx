import { EMPTY_LOG_FILTERS, activeFilters, localDateTimeToUtc, utcToLocalDateTime, type LogFilterState } from './logFilters';
import './logFilterControls.css';

const SEVERITIES = ['DEBUG', 'INFO', 'WARN', 'ERROR'];

type Props = {
  filters: LogFilterState;
  onChange: (filters: LogFilterState) => void;
};

/**
 * Presentational, controlled filter UI. SCRUM-53 owns the /api/logs request;
 * this component only exposes state in the API's exact filter vocabulary.
 */
export default function LogFilterControls({ filters, onChange }: Props) {
  const selected = activeFilters(filters);

  function setFilter(key: keyof LogFilterState, value: string) {
    onChange({ ...filters, [key]: value });
  }

  return (
    <section className="log-filters card" aria-labelledby="log-filter-heading">
      <div className="log-filters-title">
        <div>
          <h2 id="log-filter-heading">Filter logs</h2>
          <p className="muted">Filters are combined, so matching logs meet every selected condition.</p>
        </div>
        <button className="btn ghost" type="button" onClick={() => onChange({ ...EMPTY_LOG_FILTERS })} disabled={selected.length === 0}>
          Clear all
        </button>
      </div>

      <div className="filter-fields">
        <label className="filter-field">
          <span>Device ID</span>
          <input value={filters.deviceId} onChange={(event) => setFilter('deviceId', event.target.value)} placeholder="Exact device ID" />
        </label>
        <label className="filter-field">
          <span>Severity</span>
          <select value={filters.severity} onChange={(event) => setFilter('severity', event.target.value)}>
            <option value="">All severities</option>
            {SEVERITIES.map((severity) => <option key={severity} value={severity}>{severity}</option>)}
          </select>
        </label>
        <label className="filter-field">
          <span>Category</span>
          <input value={filters.category} onChange={(event) => setFilter('category', event.target.value)} placeholder="Exact category" />
        </label>
        <fieldset className="time-range-field">
          <legend>Time range</legend>
          <p className="muted">Local input is sent to the API as UTC. Start is inclusive; end is exclusive.</p>
          <div className="time-inputs">
            <label className="filter-field">
              <span>Start</span>
              <input type="datetime-local" value={utcToLocalDateTime(filters.start)} onChange={(event) => setFilter('start', localDateTimeToUtc(event.target.value))} />
            </label>
            <label className="filter-field">
              <span>End</span>
              <input type="datetime-local" value={utcToLocalDateTime(filters.end)} onChange={(event) => setFilter('end', localDateTimeToUtc(event.target.value))} />
            </label>
          </div>
        </fieldset>
      </div>

      <div className="active-filter-area" aria-live="polite">
        <strong>Active filters</strong>
        {selected.length === 0 ? (
          <span className="muted">None — showing all logs.</span>
        ) : (
          <ul className="active-filter-list" aria-label="Active filters">
            {selected.map((filter) => (
              <li key={filter.key}>
                <button type="button" className="filter-chip" onClick={() => setFilter(filter.key, "")} aria-label={`Remove ${filter.label} filter: ${filter.value}`}>
                  <span>{filter.label}: {filter.value}</span><span aria-hidden="true">×</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
