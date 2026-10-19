import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { useEffect } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import type { User } from "../api";
import { App } from "../App";
import { AuthProvider } from "../auth/AuthProvider";
import { ThemeProvider } from "../lib/theme";
import { mockUsers } from "../mocks/fixtures";
import { mockSession } from "../mocks/handlers";
import { server } from "../mocks/server";

/** The router's current location, for asserting filters reach the URL. */
export const location = { current: "" };

function LocationProbe() {
  const loc = useLocation();
  useEffect(() => {
    location.current = loc.pathname + loc.search;
  }, [loc]);
  return null;
}

/** Renders the whole app at `route`, logged in as `as` (or logged out with null). */
export function renderApp(route = "/changes", { as = "admin" }: { as?: "admin" | "viewer" | null } = {}) {
  mockSession.user = as ? (mockUsers[as]!.user as User) : null;
  const user = userEvent.setup();
  const result = render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>
          <App />
          <LocationProbe />
        </AuthProvider>
      </MemoryRouter>
    </ThemeProvider>,
  );
  return { user, ...result };
}

/** Records the URL of every request to `path` (then lets the normal handler answer). */
export function recordRequests(path: string): URL[] {
  const seen: URL[] = [];
  server.use(
    http.all(`*/api/v1${path}`, ({ request }) => {
      seen.push(new URL(request.url));
    }),
  );
  return seen;
}
