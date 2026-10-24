// `npm run dev:mock` and the demo build: answers every /api request in the browser. In dev:mock the
// logged-in mock user is kept in sessionStorage so a reload doesn't log out; it is a role name, not
// a credential. The demo build keeps nothing: it starts signed in as the demo admin every time.
import { HttpResponse, http } from "msw";
import { setupWorker } from "msw/browser";
import type { IntakeHealth, User } from "../api";
import { DEMO_USER } from "../demo/links";
import { DEMO } from "../lib/mode";
import { buildIntakeHealth } from "./fixtures";
import { handlers, mockDb, mockSession } from "./handlers";

const KEY = "ct-mock-user";

declare global {
  interface Window {
    /**
     * Seeds the mock before it starts. The Playwright tests (e2e/) set it with addInitScript,
     * because the service worker answers /api before Playwright's own routing can see it.
     */
    __ctMock?: { intake?: Partial<IntakeHealth> };
  }
}

/** The demo build only: an /api call no handler claims fails loudly, like vite.config.ts's guard. */
const bypassGuard = http.all("*/api/*", () =>
  HttpResponse.json(
    { code: "mock_bypassed", message: "The request has no mock handler; reload the demo." },
    { status: 503 },
  ),
);

/** The recorded demo: signed in as an admin, intake in Replay with its own explanation. */
function startDemoState(): void {
  mockSession.user = { ...DEMO_USER };
  mockDb.intake = buildIntakeHealth({
    state: "replay",
    reasons: ["Recorded: events replayed from fixtures"],
    grafana_url: null,
  });
}

export async function startMockWorker(): Promise<void> {
  // The demo starts fresh and signed in on every load: nothing is saved, so a reload is the reset.
  if (DEMO) startDemoState();
  else restoreSession();
  await setupWorker(...handlers, ...(DEMO ? [bypassGuard] : [])).start({
    // Under the site's base path, which may not be the domain root.
    serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` },
    onUnhandledFrame: "bypass",
    quiet: true,
  });
}

function restoreSession(): void {
  try {
    const saved = sessionStorage.getItem(KEY);
    if (saved) mockSession.user = JSON.parse(saved) as User;
  } catch {
    // Storage blocked or bad JSON: start logged out.
  }
  if (window.__ctMock?.intake) mockDb.intake = buildIntakeHealth(window.__ctMock.intake);
  mockSession.onChange = (user) => {
    try {
      if (user) sessionStorage.setItem(KEY, JSON.stringify(user));
      else sessionStorage.removeItem(KEY);
    } catch {
      // Storage blocked: the session lasts until reload.
    }
  };
}
