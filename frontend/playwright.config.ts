// Browser tests (e2e/) against `npm run dev:mock`: MSW answers every /api call in the page, so
// no backend runs and nothing leaves localhost (e2e/fixtures.ts fails any test that tries).
// Chromium only. CHROMIUM_PATH points at a system Chromium (the devcontainer sets it); without
// it, Playwright uses its own, from `npx playwright install chromium`.
import { defineConfig, devices } from "@playwright/test";

const PORT = 5173;
const BASE_URL = `http://127.0.0.1:${PORT}`;
// The demo build served by `vite preview`: the built site, as GitHub Pages serves it.
const DEMO_PORT = 4173;
const DEMO_URL = `http://127.0.0.1:${DEMO_PORT}`;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      executablePath: process.env.CHROMIUM_PATH || undefined,
      // Second guard behind the route check in e2e/fixtures.ts: every hostname but localhost
      // fails to resolve, so nothing outside this machine is reachable even by accident.
      args: ["--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1"],
    },
  },
  projects: [
    {
      name: "e2e",
      testIgnore: ["screenshots.spec.ts", "demo*.spec.ts"],
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The recorded demo site (npm run build:demo), not the dev server.
      name: "demo",
      testMatch: "demo.spec.ts",
      use: { ...devices["Desktop Chrome"], baseURL: DEMO_URL },
    },
    {
      // `make screenshots` only: rewrites docs/screenshots/demo-*.png from the demo build.
      name: "demo-screenshots",
      testMatch: "demo-screenshots.spec.ts",
      use: { ...devices["Desktop Chrome"], baseURL: DEMO_URL, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 },
    },
    {
      // `make screenshots` only: rewrites docs/screenshots/.
      name: "screenshots",
      testMatch: "screenshots.spec.ts",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 },
    },
  ],
  webServer: [
    {
      // --host: Vite's default "localhost" can resolve to ::1 only, and then 127.0.0.1 never
      // answers. Still loopback-only.
      command: `npm run dev:mock -- --host 127.0.0.1 --port ${PORT} --strictPort`,
      url: BASE_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: `npm run build:demo && npm run preview:demo -- --host 127.0.0.1 --port ${DEMO_PORT} --strictPort`,
      url: DEMO_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
