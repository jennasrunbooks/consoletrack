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

    // Matches docs/mockups/change-details.png.
    test("change details", async ({ page }) => {
      await logIn(page, "admin");
      await page.goto("/changes/42");
      await expect(page.getByRole("heading", { level: 1, name: /SSH opened/ })).toBeVisible();
      await expect(page.getByText(/^get_resource_changes\(/)).toBeVisible();
      await expect(page.locator(".intake-button")).toHaveAttribute("data-state", "live");
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${OUT}change-details${suffix}.png`, fullPage: true, animations: "disabled" });
    });

    test("postmortem editor", async ({ page }) => {
      await logIn(page, "admin");
      await page.goto("/incidents/14");
      await page.getByRole("button", { name: "Edit postmortem" }).click();
      await page.getByRole("tab", { name: "Preview" }).click();
      const card = page.locator("section.card").filter({ has: page.getByRole("heading", { name: "Postmortem", exact: true }) });
      await page.evaluate(() => document.fonts.ready);
      // A full-page capture clipped to the card: an element screenshot scrolls, and the sticky
      // header would cover the card's top.
      const box = (await card.boundingBox())!;
      const scrollY = await page.evaluate(() => window.scrollY);
      const pad = 16;
      await page.screenshot({
        path: `${OUT}postmortem${suffix}.png`,
        fullPage: true,
        animations: "disabled",
        clip: { x: box.x - pad, y: box.y + scrollY - pad, width: box.width + 2 * pad, height: box.height + 2 * pad },
      });
    });

    test("incidents", async ({ page }) => {
      await logIn(page, "admin");
      await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Incidents" }).click();
      await expect(page.getByRole("link", { name: "DemoAppHighErrorRate" }).first()).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${OUT}incidents${suffix}.png`, fullPage: true, animations: "disabled" });
    });

    test("incident detail", async ({ page }) => {
      await logIn(page, "admin");
      await page.goto("/incidents/14");
      await expect(page.getByRole("heading", { level: 1, name: "DemoAppHighErrorRate" })).toBeVisible();
      await expect(page.getByText(/^get_pods\(/).first()).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${OUT}incident-detail${suffix}.png`, fullPage: true, animations: "disabled" });
    });
  });
}
