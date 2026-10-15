import { useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { CHECKS, runChecks, type CheckResult } from './checks';
import './prerequisites.css';

type ItemState = 'pending' | 'checking' | 'pass' | 'fail';

const STATUS_TEXT: Record<ItemState, string> = {
  pending: '○ Pending',
  checking: '… Checking',
  pass: '✓ Pass',
  fail: '✗ Fail',
};

const allIds = CHECKS.map((c) => c.id as string);

export default function PrerequisitesPage() {
  const { user, logout } = useAuth();
  const [results, setResults] = useState<Record<string, CheckResult>>({});
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set(allIds));
  const [error, setError] = useState<string | null>(null);
  const [showIdleNotice, setShowIdleNotice] = useState(true);

  const busy = running.size > 0;

  async function run() {
    const ids = allIds.filter((id) => selected.has(id));
    if (ids.length === 0) return;
    setRunning(new Set(ids));
    setError(null);
    try {
      const fresh = await runChecks(ids);
      // Merge so checks that weren't re-run keep their previous result.
      setResults((prev) => ({ ...prev, ...Object.fromEntries(fresh.map((r) => [r.id, r])) }));
    } catch {
      setError('Could not run prerequisite checks. Try again.');
    } finally {
      setRunning(new Set());
    }
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const stateOf = (id: string): ItemState =>
    running.has(id) ? 'checking' : results[id]?.status ?? 'pending';

  const failed = allIds.filter((id) => results[id]?.status === 'fail').length;
  const notRun = allIds.filter((id) => !results[id]).length;
  const allPass = notRun === 0 && failed === 0;
  const ranAny = Object.keys(results).length > 0;

  let banner = 'Checks have not been run yet';
  if (busy) banner = 'Checking prerequisites…';
  else if (error) banner = error;
  else if (allPass) banner = '✓ All prerequisites met';
  else if (failed > 0) banner = `✗ ${failed} requirement${failed === 1 ? '' : 's'} missing`;
  else if (ranAny) banner = `${notRun} check${notRun === 1 ? '' : 's'} not run yet`;

  const bannerClass = allPass ? 'ok' : failed > 0 || error ? 'bad' : '';
  const label = busy
    ? 'Checking…'
    : ranAny
      ? `Re-run ${selected.size} selected`
      : `Run ${selected.size} selected`;

  return (
    <>
      <header className="app-header">
        <span>Portrait IQ</span>
        <span className="header-user">
          <span>
            Signed in as <strong>{user?.username}</strong>
          </span>
          <button className="btn ghost" onClick={() => logout()}>Sign out</button>
        </span>
      </header>
      <main className="prereq">
        {showIdleNotice && (
          <div className="notice" role="note">
            <span>
              <strong>Heads up:</strong> you'll be signed out automatically after 5 minutes of
              inactivity.
            </span>
            <button
              className="notice-close"
              onClick={() => setShowIdleNotice(false)}
              aria-label="Dismiss inactivity notice"
            >
              ×
            </button>
          </div>
        )}
        <h2>Environment prerequisites</h2>
        <div className={`prereq-banner ${bannerClass}`} role="status" aria-live="polite">
          {banner}
        </div>
        <div className="prereq-actions">
          <button className="btn" onClick={run} disabled={busy || selected.size === 0}>
            {label}
          </button>
          <button className="btn ghost" disabled={busy} onClick={() => setSelected(new Set(allIds))}>
            Select all
          </button>
          <button className="btn ghost" disabled={busy} onClick={() => setSelected(new Set())}>
            Select none
          </button>
          <button className="btn" disabled={!allPass || busy}>Start run</button>
        </div>
        <ul className="prereq-list" aria-live="polite">
          {CHECKS.map((c) => {
            const state = stateOf(c.id);
            const msg = results[c.id]?.message;
            return (
              <li key={c.id} className={`prereq-item ${state}`}>
                <div className="prereq-row">
                  <label className="prereq-pick">
                    <input
                      type="checkbox"
                      checked={selected.has(c.id)}
                      disabled={busy}
                      onChange={() => toggle(c.id)}
                    />
                    <span>{c.label}</span>
                  </label>
                  <span className="prereq-status">{STATUS_TEXT[state]}</span>
                </div>
                {state === 'fail' && msg && <p className="prereq-msg">{msg}</p>}
              </li>
            );
          })}
        </ul>
      </main>
    </>
  );
}
