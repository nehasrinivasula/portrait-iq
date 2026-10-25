import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import IdleWarning from "./auth/IdleWarning";
import LoginPage from "./features/login/LoginPage";
import IngestionPage from "./features/ingestion/IngestionPage";
import PrerequisitesPage from "./features/prerequisites/PrerequisitesPage";
import type { ReactNode } from "react";

function Protected({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  if (!ready) return null;
  return user ? <>{children}</> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <>
    <IdleWarning />
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/prerequisites" element={<Protected><PrerequisitesPage /></Protected>} />
      <Route path="/ingest" element={<Protected><IngestionPage /></Protected>} />
      <Route path="*" element={<Navigate to="/prerequisites" replace />} />
    </Routes>
    </>
  );
}
