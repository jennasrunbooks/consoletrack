import type { Page } from "@playwright/test";
import { expect, logIn, test } from "./fixtures";

// WCAG 2 relative luminance and contrast ratio, from the computed `rgb(...)` colors.
function luminance(rgb: string): number {
  const [r, g, b] = (rgb.match(/[\d.]+/g) ?? []).slice(0, 3).map((v) => {
    const c = Number(v) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

async function selectedChipColors(page: Page) {
  const chip = page.locator('.chip[aria-pressed="true"]');
  await expect(chip).toHaveCount(1);
  return chip.evaluate((el) => {
    const style = getComputedStyle(el);
    return { color: style.color, background: style.backgroundColor };
  });
}

for (const theme of ["light", "dark"] as const) {
  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    test("the selected chip is readable", async ({ page }) => {
      await logIn(page, "viewer");
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);

      for (const name of ["All (6)", "Awaiting review (2)", "Closed (3)"]) {
        await page.getByRole("button", { name }).click();
        await expect(page.getByRole("button", { name })).toHaveAttribute("aria-pressed", "true");
        const { color, background } = await selectedChipColors(page);
        // A transparent background would mean the chip text sits on the page instead.
        expect(background, `${name} background`).not.toBe("rgba(0, 0, 0, 0)");
        expect(contrast(color, background), `${name}: ${color} on ${background}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  });
}

test("the toggle switches theme and remembers it", async ({ page }) => {
  await logIn(page, "viewer");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
});
