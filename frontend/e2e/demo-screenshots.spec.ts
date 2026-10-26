// `npm run screenshots:demo` (the "demo-screenshots" project) rewrites docs/screenshots/demo-*.png
// from the demo build: the banner and the first-visit tour, desktop and phone width, both themes.
import { fileURLToPath } from "node:url";
import { expect, test } from "./fixtures";

const OUT = fileURLToPath(new URL("../../docs/screenshots/", import.meta.url));

for (const theme of ["light", "dark"] as const) {
  const suffix = theme === "dark" ? "-dark" : "";

  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    test("banner and tour", async ({ page }) => {
      await page.goto("/changes");
      await expect(page.getByRole("region", { name: "Take the 3-minute tour" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Change log" })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({
        path: `${OUT}demo-tour${suffix}.png`,
        animations: "disabled",
        clip: { x: 0, y: 0, width: 1440, height: 520 },
      });
    });

    test("banner at phone width", async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/changes");
      await expect(page.getByRole("region", { name: "Take the 3-minute tour" })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${OUT}demo-mobile${suffix}.png`, animations: "disabled" });
    });
  });
}
