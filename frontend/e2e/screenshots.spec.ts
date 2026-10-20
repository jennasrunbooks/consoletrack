// `make screenshots` (the "screenshots" project) rewrites docs/screenshots/, named like the
// mockup in docs/mockups/ each one matches. Not part of `make test-e2e`.
import { fileURLToPath } from "node:url";
import { expect, logIn, test } from "./fixtures";

const OUT = fileURLToPath(new URL("../../docs/screenshots/", import.meta.url));

for (const theme of ["light", "dark"] as const) {
  const suffix = theme === "dark" ? "-dark" : "";

  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    test("login", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${OUT}login${suffix}.png`, fullPage: true, animations: "disabled" });
    });

    test("change log", async ({ page }) => {
      await logIn(page, "admin");
      await expect(page.locator(".intake-button")).toHaveAttribute("data-state", "live");
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${OUT}change-log${suffix}.png`, fullPage: true, animations: "disabled" });
    });

    test("incidents", async ({ page }) => {
      await logIn(page, "admin");
      await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Incidents" }).click();
      await expect(page.getByRole("link", { name: "KubePodOOMKilled" })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${OUT}incidents${suffix}.png`, fullPage: true, animations: "disabled" });
    });

    test("incident detail", async ({ page }) => {
      await logIn(page, "admin");
      await page.goto("/incidents/14");
      await expect(page.getByRole("heading", { level: 1, name: "KubePodOOMKilled" })).toBeVisible();
      await expect(page.getByText(/^get_pods\(/)).toBeVisible();
      // Open one result so the screenshot shows how evidence reads.
      await page.getByText(/^get_events\(/).click();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${OUT}incident-detail${suffix}.png`, fullPage: true, animations: "disabled" });
    });
  });
}
