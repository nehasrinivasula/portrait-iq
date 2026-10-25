import HealthBadge from './HealthBadge';
import './system.css';

// Pre-login this shows only whether the server is up, so "the box is down" is
// distinguishable from "wrong password". Box details stay behind sign-in.
export default function SystemInfoPanel() {
  return (
    <section className="sysinfo" aria-label="Server status">
      <h3>Server</h3>
      <dl>
        <div><dt>Status</dt><dd><HealthBadge /></dd></div>
      </dl>
    </section>
  );
}
