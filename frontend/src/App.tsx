import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import IdleWarning from "./auth/IdleWarning";
import LoginPage from "./features/login/LoginPage";
import HomePage from "./features/home/HomePage";
import RunPage from "./features/plugins/RunPage";
import LogsPage from "./features/logs/LogsPage";
import AuditPage from "./features/audit/AuditPage";
import JobResultsPage from "./features/jobs/JobResultsPage";
import ExportPage from "./features/export/ExportPage";
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
            <Route path="audit" element={<AuditPage />} />
            <Route path="jobs/:jobId/results" element={<JobResultsPage />} />
            <Route path="jobs/:jobId/export" element={<ExportPage />} />
            <Route path="export/:jobId" element={<ExportPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
