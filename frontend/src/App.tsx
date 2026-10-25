import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import IdleWarning from "./auth/IdleWarning";
import LoginPage from "./features/login/LoginPage";
import HomePage from "./features/home/HomePage";
import RunPage from "./features/plugins/RunPage";
import LogsPage from "./features/logs/LogsPage";
import AppLayout from "./ui/AppLayout";
import { Outlet } from "react-router-dom";

function Protected() {
  const { user, ready } = useAuth();
  if (!ready) return null;
  return user ? <Outlet /> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <>
      <IdleWarning />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<Protected />}>
          <Route element={<AppLayout />}>
            <Route index element={<HomePage />} />
            <Route path="run" element={<RunPage />} />
            <Route path="logs" element={<LogsPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
