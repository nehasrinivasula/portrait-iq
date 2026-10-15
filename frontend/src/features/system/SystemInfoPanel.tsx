import { useEffect, useState } from 'react';
import { getSystemInfo, type SystemInfo } from './systemInfo';
import HealthBadge from './HealthBadge';
import './system.css';

export default function SystemInfoPanel() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    getSystemInfo().then(setInfo).catch(() => {});
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);

  if (!info) return null;
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return (
    <section className="sysinfo" aria-label="This box">
      <h3>This box</h3>
      <dl>
        <div><dt>Status</dt><dd><HealthBadge /></dd></div>
        <div><dt>Box</dt><dd>{info.boxId} · {info.hostname}</dd></div>
        <div><dt>Device network</dt><dd>{info.captureInterface.name} · {info.captureInterface.address}</dd></div>
        <div><dt>Time</dt><dd>{now.toLocaleString()} ({tz})</dd></div>
        <div><dt>Version</dt><dd>{info.appVersion}</dd></div>
      </dl>
    </section>
  );
}
