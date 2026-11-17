import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import CleanupDialog from "../features/cleanup/CleanupDialog";
import { getCleanup } from "../features/cleanup/cleanupApi";

const links = [
  { to: "/", label: "Home", end: true },
  { to: "/run", label: "Run diagnostics" },
  { to: "/logs", label: "Logs" },
  { to: "/audit", label: "Audit log" },
];

export default function AppLayout() {
  const { user, signOut } = useAuth();
  const { pathname } = useLocation();
  const [ending, setEnding] = useState(false);       // the sign-out cleanup prompt
  const [recovery, setRecovery] = useState(false);   // leftovers found when the app opened

  // Once after sign-in or a refresh: if a crash or restart left tool pods or
  // files that nobody has chosen about, ask what to do with them.
  useEffect(() => {
    let live = true;
    getCleanup().then((s) => { if (live && s.state === "pending") setRecovery(true); }).catch(() => undefined);
    return () => { live = false; };
  }, []);

  return (
    <>
      <header className="app-header">
        <strong>Portrait IQ</strong>
        <nav className="app-nav" aria-label="Main">
          {links.map((l) => <NavLink key={l.to} to={l.to} end={l.end}>{l.label}</NavLink>)}
        </nav>
        <span>
          {user?.name} <button className="btn ghost" onClick={() => setEnding(true)}>Sign out</button>
        </span>
      </header>
      <main className={`page${pathname.startsWith("/logs") ? " wide" : ""}`}><Outlet /></main>
      {recovery && !ending && <CleanupDialog context="restart" onClose={() => setRecovery(false)} />}
      {ending && <CleanupDialog context="logout" onClose={() => setEnding(false)} onSignOut={(undecided) => void signOut(undecided)} />}
    </>
  );
}
