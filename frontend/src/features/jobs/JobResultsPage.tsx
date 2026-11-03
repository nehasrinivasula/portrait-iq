import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { apiFetch } from '../../api';
import { useAuth } from '../../auth/AuthContext';
import { netshootSummary, type PluginResult } from './netshootResults';
import './jobResults.css';

type Job = { id: string; status: string };

export default function JobResultsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const { user, logout } = useAuth();
  const [job, setJob] = useState<Job | null>(null);
  const [results, setResults] = useState<PluginResult[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let active = true;
    let timer: number | undefined;
    async function refresh() {
      try {
        const [nextJob, nextResults] = await Promise.all([
          apiFetch<Job>(`/api/jobs/${encodeURIComponent(jobId!)}`),
          apiFetch<PluginResult[]>(`/api/jobs/${encodeURIComponent(jobId!)}/results`),
        ]);
        if (!active) return;
        setJob(nextJob);
        setResults(nextResults);
        setError(null);
        if (nextJob.status === 'queued' || nextJob.status === 'running') {
          timer = window.setTimeout(refresh, 2000);
        }
      } catch {
        if (active) setError('Could not load this run. Check the job ID and try again.');
      }
    }
    void refresh();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId]);

  const netshoot = results.find((result) => result.tool === 'netshoot');
  const checks = netshoot?.checks ?? [];
  const running = job?.status === 'queued' || job?.status === 'running';

  return (
    <>
      <header className="app-header">
        <span>Portrait IQ</span>
        <span className="header-user">
          <span>Signed in as <strong>{user?.username}</strong></span>
          <button className="btn ghost" onClick={() => logout()}>Sign out</button>
        </span>
      </header>
      <main className="job-results">
        <Link to="/prerequisites">← Back to prerequisites</Link>
        <h2>Netshoot network checks</h2>
        <p className="muted">Job {jobId}</p>
        {error && <p role="alert" className="job-results-error">{error}</p>}
        {job && <p role="status">Run status: {job.status}</p>}
        {netshoot?.error && <p role="alert" className="job-results-error">Netshoot could not run: {netshoot.error}</p>}
        {checks.length > 0 ? (
          <>
            <p className="job-results-summary">{netshootSummary(checks)}</p>
            <ol className="job-results-list">
              {checks.map((check, index) => (
                <li key={`${index}-${check.label}`} className={`job-check ${check.status}`}>
                  <span>{check.label}</span>
                  <strong>{check.status === 'pass' ? '✓ Pass' : '✗ Fail'}</strong>
                  {check.status === 'fail' && check.reason && <p>{check.reason}</p>}
                </li>
              ))}
            </ol>
          </>
        ) : !error && !netshoot?.error && (
          <p>{running ? 'Netshoot checks are still running…' : 'No per-check Netshoot results are available for this job.'}</p>
        )}
      </main>
    </>
  );
}
