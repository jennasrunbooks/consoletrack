// Browser tests (e2e/) against `npm run dev:mock`: MSW answers every /api call in the page, so
// no backend runs and nothing leaves localhost (e2e/fixtures.ts fails any test that tries).
// Chromium only. CHROMIUM_PATH points at a system Chromium (the devcontainer sets it); without
// it, Playwright uses its own, from `npx playwright install chromium`.
import { defineConfig, devices } from "@playwright/test";

const PORT = 5173;
const BASE_URL = `http://127.0.0.1:${PORT}`;

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
      testIgnore: "screenshots.spec.ts",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // `make screenshots` only: rewrites docs/screenshots/.
      name: "screenshots",
      testMatch: "screenshots.spec.ts",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 },
    },
  ],
  webServer: {
    // --host: Vite's default "localhost" can resolve to ::1 only, and then 127.0.0.1 never
    // answers. Still loopback-only.
    command: `npm run dev:mock -- --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
