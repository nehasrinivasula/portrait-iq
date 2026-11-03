import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const links = [
  { to: "/", label: "Home", end: true },
  { to: "/run", label: "Run diagnostics" },
  { to: "/logs", label: "Logs" },
];

export default function AppLayout() {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  return (
    <>
      <header className="app-header">
        <strong>Portrait IQ</strong>
        <nav className="app-nav" aria-label="Main">
          {links.map((l) => <NavLink key={l.to} to={l.to} end={l.end}>{l.label}</NavLink>)}
        </nav>
        <span>
          {user?.name} <button className="btn ghost" onClick={() => logout()}>Sign out</button>
        </span>
      </header>
      <main className={`page${pathname.startsWith("/logs") ? " wide" : ""}`}><Outlet /></main>
    </>
  );
}
