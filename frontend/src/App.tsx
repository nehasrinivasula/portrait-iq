import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import LoginPage from "./features/login/LoginPage";
import PrerequisitesPage from "./features/prerequisites/PrerequisitesPage";
import type { ReactNode } from "react";

function Protected({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  if (!ready) return null;
  return user ? <>{children}</> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/prerequisites" element={<Protected><PrerequisitesPage /></Protected>} />
      <Route path="*" element={<Navigate to="/prerequisites" replace />} />
    </Routes>
  );
}
