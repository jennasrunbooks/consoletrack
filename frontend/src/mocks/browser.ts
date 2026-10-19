// `npm run dev:mock` only: answers every /api request in the browser. The logged-in mock user
// is kept in sessionStorage so a reload doesn't log out; it is a role name, not a credential.
import { setupWorker } from "msw/browser";
import type { IntakeHealth, User } from "../api";
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

export async function startMockWorker(): Promise<void> {
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
  await setupWorker(...handlers).start({ onUnhandledFrame: "bypass", quiet: true });
}
