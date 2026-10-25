import { useAuth } from "../../auth/AuthContext";
import SystemInfoPanel from "../system/SystemInfoPanel";

// MVP landing screen after sign-in. Source / time-frame, plugin selection and the
// log table (SCRUM-45, 87, 52) mount here as they land.
export default function CollectPage() {
  const { user, logout } = useAuth();
  return (
    <main className="card" style={{ maxWidth: 960, margin: "2rem auto" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Collect logs</h1>
        <span>
          {user?.name} <button className="btn ghost" onClick={() => logout()}>Sign out</button>
        </span>
      </header>
      <SystemInfoPanel />
    </main>
  );
}
