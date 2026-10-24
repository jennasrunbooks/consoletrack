// axe-core (WCAG 2.1 A and AA, color contrast included) on every screen in both themes, plus the
// states that only appear after a click: the intake panel, the revert dialog, the postmortem editor.
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, logIn, test } from "./fixtures";

async function expectNoViolations(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  const { violations } = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const summary = violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    nodes: v.nodes.map((n) => `${n.target.join(" ")}: ${n.failureSummary?.split("\n")[1]?.trim() ?? ""}`),
  }));
  expect(summary).toEqual([]);
}

for (const theme of ["light", "dark"] as const) {
  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    test("login", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
      await expectNoViolations(page);
    });

    test("change log, with the intake panel open", async ({ page }) => {
      await logIn(page, "admin");
      await expect(page.locator(".intake-button")).toHaveAttribute("data-state", "live");
      await expectNoViolations(page);
      await page.locator(".intake-button").click();
      await expectNoViolations(page);
    });

    for (const id of [42, 41, 39]) {
      test(`change detail CHG-${id}`, async ({ page }) => {
        await logIn(page, "admin");
        await page.goto(`/changes/${id}`);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expect(page.locator(".evidence, .card .muted").first()).toBeVisible();
        await expectNoViolations(page);
      });
    }

    test("change detail as a viewer", async ({ page }) => {
      await logIn(page, "viewer");
      await page.goto("/changes/42");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoViolations(page);
    });

    test("the revert dialog", async ({ page }) => {
      await logIn(page, "admin");
      await page.goto("/changes/42");
      await page.getByRole("button", { name: "Revert to Terraform" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await expectNoViolations(page);
    });

    test("incidents", async ({ page }) => {
      await logIn(page, "admin");
      await page.goto("/incidents");
      await expect(page.getByRole("link", { name: "DemoAppHighErrorRate" }).first()).toBeVisible();
      await expectNoViolations(page);
    });

    for (const id of [14, 13, 11]) {
      test(`incident detail INC-${id}`, async ({ page }) => {
        await logIn(page, "admin");
        await page.goto(`/incidents/${id}`);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expectNoViolations(page);
      });
    }

    test("postmortem editor, write and preview", async ({ page }) => {
      await logIn(page, "admin");
      await page.goto("/incidents/14");
      await page.getByRole("button", { name: "Edit postmortem" }).click();
      await expectNoViolations(page);
      await page.getByRole("tab", { name: "Preview" }).click();
      await expectNoViolations(page);
    });

    test("empty and not-found states", async ({ page }) => {
      await logIn(page, "admin");
      await page.goto("/changes?q=no-such-change");
      await expect(page.getByText(/No changes match/i)).toBeVisible();
      await expectNoViolations(page);
      await page.goto("/changes/999");
      await expect(page.getByRole("heading", { name: "Change not found" })).toBeVisible();
      await expectNoViolations(page);
    });
  });
}
