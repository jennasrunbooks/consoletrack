import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { mockDb, mockSession } from "../mocks/handlers";
import { server } from "../mocks/server";

// Tests never touch the network: any request without a handler fails the test.
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  mockDb.reset();
  mockSession.user = null;
  try {
    localStorage.clear();
  } catch {
    // ignore
  }
  document.documentElement.removeAttribute("data-theme");
});
afterAll(() => server.close());

// jsdom has no matchMedia; the theme reads prefers-color-scheme through it.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
