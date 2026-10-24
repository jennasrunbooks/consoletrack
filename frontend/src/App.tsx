import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthProvider";
import { AppShell } from "./components/AppShell";
import { DemoErrorPage } from "./demo/DemoErrorPage";
import { RestartDemoPage } from "./demo/RestartDemoPage";
import { DEMO } from "./lib/mode";
import { ChangeDetailPage } from "./pages/changes/ChangeDetailPage";
import { ChangeLogPage } from "./pages/changes/ChangeLogPage";
import { LoginPage } from "./pages/LoginPage";
import { IncidentDetailPage } from "./pages/incidents/IncidentDetailPage";
import { IncidentListPage } from "./pages/incidents/IncidentListPage";
import { NotFoundPage } from "./pages/PlaceholderPage";

export function App() {
  const { user, error } = useAuth();
  if (user === undefined) {
    return (
      <div className="boot" role="status">
        Loading…
      </div>
    );
  }
  // The demo has no login form: it starts signed in, logging out ends it, and any failure to
  // reach the mock (a request that skipped the worker) asks for a reload.
  if (DEMO && error) return <DemoErrorPage />;
  if (user === null) return DEMO ? <RestartDemoPage /> : <LoginPage />;
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/changes" replace />} />
        <Route path="changes" element={<ChangeLogPage />} />
        <Route path="changes/:id" element={<ChangeDetailPage />} />
        <Route path="incidents" element={<IncidentListPage />} />
        <Route path="incidents/:id" element={<IncidentDetailPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
