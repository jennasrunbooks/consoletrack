// The recorded demo site: `npm run build:demo`, served by `vite preview` (see the "demo" project
// in playwright.config.ts). Nothing here logs in, and nothing may reach /api over the network:
// the service worker in the page answers every call.
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, test as base } from "./fixtures";

const BANNER = "Recorded demo · sample data, no live AWS";

/** The banner, then the page's own heading, so a blank page can't pass. */
async function expectBanner(page: Page): Promise<void> {
  const banner = page.getByRole("complementary", { name: "About this demo" });
  await expect(banner).toContainText(BANNER);
  await expect(banner.getByRole("link", { name: "How it works" })).toHaveAttribute("href", /#how-it-works$/);
  await expect(banner.getByRole("link", { name: "Source" })).toBeVisible();
}

// Every /api response the page sees must come from the service worker. A call that got past it
// would hit the preview server's guard (503 mock_bypassed) and break the page as well.
const test = base.extend<{ apiLog: void }>({
  apiLog: [
    async ({ page }, use) => {
      const network: string[] = [];
      page.on("response", (res) => {
        if (new URL(res.url()).pathname.includes("/api/") && !res.fromServiceWorker()) network.push(res.url());
      });
      await use();
      expect(network, "/api responses that did not come from the service worker").toEqual([]);
    },
    { auto: true },
  ],
});

test("starts signed in as an admin, with no login form and no credentials", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/changes$/);
  await expect(page.getByRole("heading", { name: "Change log" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Log in" })).toHaveCount(0);
  await expect(page.getByLabel("Password")).toHaveCount(0);
  await expect(page.getByText(/admin\/admin/)).toHaveCount(0);
  await page.getByRole("button", { name: "Account: demo" }).click();
  await expect(page.getByRole("region", { name: "Account" })).toContainText("Admin: can revert, accept, and approve.");
});

test("the banner is on every page", async ({ page }) => {
  for (const [path, heading] of [
    ["/changes", "Change log"],
    ["/changes/42", /SSH opened/],
    ["/incidents", "Incidents"],
    ["/incidents/14", "DemoAppHighErrorRate"],
    ["/no/such/page", /not found/i],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expectBanner(page);
  }
});

test("the banner holds its words in a phone-width layout", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto("/changes");
  await expectBanner(page);
  const banner = page.getByRole("complementary", { name: "About this demo" });
  const box = (await banner.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(375);
  expect(await banner.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  for (const link of await banner.getByRole("link").all()) await expect(link).toBeInViewport({ ratio: 1 });
});

test("a deep link survives a reload", async ({ page }) => {
  await page.goto("/incidents/14");
  await expect(page.getByRole("heading", { level: 1, name: "DemoAppHighErrorRate" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "DemoAppHighErrorRate" })).toBeVisible();
  await expectBanner(page);
});

test("intake reads as the recorded Replay state and explains itself", async ({ page }) => {
  await page.goto("/changes");
  const button = page.locator(".intake-button");
  await expect(button).toHaveAttribute("data-state", "replay");
  await expect(button).toContainText("Replay mode · recorded events");
  await button.click();
  await expect(page.getByRole("list", { name: "Why" })).toContainText("Recorded: events replayed from fixtures");
});

test("every action works against the in-memory state, and a reload resets it", async ({ page }) => {
  // Accept a change with a note.
  await page.goto("/changes/42");
  await page.getByLabel("Note (required to accept)").fill("Opened for a one-hour debugging session");
  await page.getByRole("button", { name: "Accept with note" }).click();
  await expect(page.locator(".detail-tags .pill").nth(1)).toHaveText("Accepted");

  // Approve the recorded OOM incident's fix.
  await page.goto("/incidents/14");
  await page.getByRole("button", { name: "Approve fix" }).click();
  await expect(page.locator(".detail-tags")).toContainText("Remediating");

  // Edit and save its postmortem.
  await page.getByRole("button", { name: /(Edit|Write) postmortem/ }).click();
  await page.getByLabel("Postmortem (Markdown)").fill("## What happened\n\nThe memory limit was too low.");
  await page.getByRole("button", { name: "Save postmortem" }).click();
  await expect(page.getByText("Postmortem saved.")).toBeVisible();

  // Nothing was saved anywhere: a reload starts over.
  await page.goto("/changes/42");
  await expect(page.getByLabel("Note (required to accept)")).toBeVisible();
  await page.goto("/incidents/14");
  await expect(page.getByRole("button", { name: "Approve fix" })).toBeVisible();
});

test("Reject closes the recorded incident with a reason", async ({ page }) => {
  await page.goto("/incidents/14");
  await page.getByLabel("Reason (required to reject)").fill("Raise the limit in Terraform instead");
  await page.getByRole("button", { name: "Reject and close" }).click();
  await expect(page.locator(".detail-tags")).toContainText("Closed");
});

test("the first-visit tour has three steps, and dismissing it sticks", async ({ page }) => {
  await page.goto("/changes");
  const tour = page.getByRole("region", { name: "Take the 3-minute tour" });
  await expect(tour.getByRole("listitem")).toHaveCount(3);
  await tour.getByRole("link", { name: /INC-11/ }).click();
  await expect(page).toHaveURL(/\/incidents\/11$/);
  await expect(page.getByRole("button", { name: "Approve fix" })).toHaveCount(0); // fixed by the policy gate
  await tour.getByRole("link", { name: /INC-14/ }).click();
  await expect(page).toHaveURL(/\/incidents\/14$/);
  await expect(tour).toBeVisible();
  await tour.getByRole("button", { name: "Dismiss" }).click();
  await expect(tour).toBeHidden();
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "DemoAppHighErrorRate" })).toBeVisible();
  await expect(tour).toBeHidden();
});

test("the tour works without storage", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", {
      get() {
        throw new DOMException("blocked", "SecurityError");
      },
    });
  });
  await page.goto("/changes");
  const tour = page.getByRole("region", { name: "Take the 3-minute tour" });
  await expect(tour).toBeVisible();
  await tour.getByRole("button", { name: "Dismiss" }).click();
  await expect(tour).toBeHidden();
});

test("logging out ends the demo at a Restart page, not a login form", async ({ page }) => {
  await page.goto("/changes");
  await page.getByRole("button", { name: "Account: demo" }).click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByRole("heading", { name: "Demo ended" })).toBeVisible();
  await expectBanner(page);
  await expect(page.getByLabel("Password")).toHaveCount(0);
  await page.getByRole("button", { name: "Restart demo" }).click();
  await expect(page.getByRole("heading", { name: "Change log" })).toBeVisible();
});

test("a request that skips the worker gets an error, not a network call", async ({ page }) => {
  await page.goto("/changes");
  await expect(page.getByRole("heading", { name: "Change log" })).toBeVisible(); // the worker is up
  const status = await page.evaluate(async () => {
    const res = await fetch("/api/v1/not-a-route");
    return { status: res.status, body: (await res.json()) as { code: string; message: string } };
  });
  // The worker's own catch-all answered ("no mock handler"), not the preview server's guard.
  expect(status).toEqual({ status: 503, body: { code: "mock_bypassed", message: expect.stringContaining("no mock handler") } });
});

test.describe("when the browser blocks service workers", () => {
  test.use({ serviceWorkers: "block" });

  test("the demo asks for a reload instead of calling /api", async ({ page }) => {
    await page.goto("/changes");
    await expect(page.getByRole("heading", { name: "Reload the demo" })).toBeVisible();
    await expectBanner(page);
  });
});

for (const theme of ["light", "dark"] as const) {
  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    test("no accessibility violations on the banner, the tour, and the Restart page", async ({ page }) => {
      const check = async () => {
        await page.evaluate(() => document.fonts.ready);
        const { violations } = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
        expect(violations.map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target.join(" ")) }))).toEqual([]);
      };
      await page.goto("/changes");
      await expect(page.getByRole("region", { name: "Take the 3-minute tour" })).toBeVisible();
      await check();
      await page.getByRole("button", { name: "Account: demo" }).click();
      await page.getByRole("button", { name: "Log out" }).click();
      await expect(page.getByRole("heading", { name: "Demo ended" })).toBeVisible();
      await check();
    });
  });
}
