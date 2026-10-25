import { useState } from "react";
import { useAuth } from "../../auth/AuthContext";
import LogFilterControls from "./LogFilterControls";
import { EMPTY_LOG_FILTERS, type LogFilterState } from "./logFilters";
import "./logFiltersPage.css";

/** Temporary route so the Filter controls UI can be reviewed before P2 connects it to log results. */
export default function LogFiltersPage() {
  const { user, logout } = useAuth();
  const [filters, setFilters] = useState<LogFilterState>(EMPTY_LOG_FILTERS);

  return (
    <>
      <header className="app-header">
        <span>Portrait IQ</span>
        <span className="header-user">
          <span>Signed in as <strong>{user?.username}</strong></span>
          <button className="btn ghost" onClick={() => logout()}>Sign out</button>
        </span>
      </header>
      <main className="log-filters-page">
        <p className="page-eyebrow">Logs</p>
        <h1>Review diagnostic logs</h1>
        <p className="muted page-intro">Choose exact-match filters to focus the current view. SCRUM-53 will apply these values to the normalized event store.</p>
        <LogFilterControls filters={filters} onChange={setFilters} />
        <div className="filter-preview card" aria-live="polite">
          <strong>Results area</strong>
          <p className="muted">This placeholder keeps P1 scoped to controls. SCRUM-53 will request <code>/api/logs</code> with the selected device_id, severity, category, start, and end values.</p>
        </div>
      </main>
    </>
  );
}
