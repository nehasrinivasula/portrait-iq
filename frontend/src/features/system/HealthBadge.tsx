import { useEffect, useState } from 'react';

type Health = 'checking' | 'up' | 'down';

// Polls the backend's /health so "is it the box or my password?" is answerable
// before the engineer tries to sign in.
export default function HealthBadge() {
  const [state, setState] = useState<Health>('checking');

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const res = await fetch('/health', { cache: 'no-store' });
        if (alive) setState(res.ok ? 'up' : 'down');
      } catch {
        if (alive) setState('down');
      }
    };
    check();
    const t = window.setInterval(check, 10_000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  const text = { checking: 'Checking server…', up: 'Server online', down: 'Server offline' }[state];
  return (
    <span className={`health ${state}`} role="status">
      {text}
      <span className="health-dot" aria-hidden="true" />
    </span>
  );
}
