// Shared test setup: a localhost-only browser context and the logged-in helpers.
import { test as base, expect, type Page } from "@playwright/test";
import type { IntakeHealth } from "../src/api";

const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

export const test = base.extend<{ offsite: string[] }>({
  // Tests never reach anything beyond localhost: requests elsewhere are aborted, and the test
  // fails listing them. data: and blob: URLs aren't network requests and pass through.
  offsite: [
    async ({ context }, use) => {
      const blocked: string[] = [];
      await context.route("**/*", (route) => {
        const url = new URL(route.request().url());
        if (url.protocol === "data:" || url.protocol === "blob:" || LOCAL_HOSTS.has(url.hostname)) {
          return route.fallback();
        }
        blocked.push(url.href);
        return route.abort("blockedbyclient");
      });
      await use(blocked);
      expect(blocked, "requests beyond localhost").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/** Starts the mock with these intake health fields; call before the first goto. */
export async function seedIntake(page: Page, intake: Partial<IntakeHealth>): Promise<void> {
  await page.addInitScript((seed) => {
    window.__ctMock = { intake: seed };
  }, intake);
}

export async function logIn(page: Page, username: "admin" | "viewer", path = "/changes"): Promise<void> {
  await page.goto(path);
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(username);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Change log" })).toBeVisible();
}

/** The change log's data rows (not the loading or empty row). */
export const changeRows = (page: Page) => page.getByRole("table").locator("tbody tr").filter({ has: page.getByRole("link") });
