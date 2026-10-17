import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthProvider";
import { AppShell } from "./components/AppShell";
import { ChangeLogPage } from "./pages/changes/ChangeLogPage";
import { LoginPage } from "./pages/LoginPage";
import { ChangeDetailPlaceholder, IncidentsPlaceholder, NotFoundPage } from "./pages/PlaceholderPage";

export function App() {
  const { user } = useAuth();
  if (user === undefined) {
    return (
      <div className="boot" role="status">
        Loading…
      </div>
    );
  }
  if (user === null) return <LoginPage />;
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/changes" replace />} />
        <Route path="changes" element={<ChangeLogPage />} />
        <Route path="changes/:id" element={<ChangeDetailPlaceholder />} />
        <Route path="incidents/*" element={<IncidentsPlaceholder />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
