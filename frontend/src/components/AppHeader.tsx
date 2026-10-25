import { NavLink } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import './app-header.css';

// The purple bar on every signed-in page: product name, page links, who is
// signed in, and sign out.
export default function AppHeader() {
  const { user, logout } = useAuth();
  return (
    <header className="app-header">
      <span className="header-brand">
        <span>Portrait IQ</span>
        <nav className="header-nav" aria-label="Main">
          <NavLink to="/prerequisites">Prerequisites</NavLink>
          <NavLink to="/ingest">Log import</NavLink>
        </nav>
      </span>
      <span className="header-user">
        <span>
          Signed in as <strong>{user?.username}</strong>
        </span>
        <button className="btn ghost" onClick={() => logout()}>Sign out</button>
      </span>
    </header>
  );
}
