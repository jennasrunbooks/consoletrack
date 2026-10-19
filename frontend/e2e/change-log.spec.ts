import { changeRows, expect, logIn, test } from "./fixtures";

// The six sample changes in src/mocks/fixtures.ts: three high risk, three closed, three IAM.
test.beforeEach(async ({ page }) => {
  await logIn(page, "viewer");
  await expect(changeRows(page)).toHaveCount(6);
});

test("loads every sample change with its counts", async ({ page }) => {
  await expect(changeRows(page).first()).toContainText("SSH opened to the internet on the demo security group");
  await expect(page.getByRole("button", { name: "All (6)" })).toHaveAttribute("aria-pressed", "true");
  const awaiting = page.getByRole("list", { name: "Summary" }).getByRole("listitem").filter({ hasText: "Awaiting review" });
  await expect(awaiting.locator(".tile-value")).toHaveText("2");
  await expect(page.getByText("Showing 6 of 6 changes")).toBeVisible();
});

test("a status chip narrows the list", async ({ page }) => {
  await page.getByRole("button", { name: "Closed (3)" }).click();
  await expect(page).toHaveURL(/status=reverted&status=accepted&status=codified/);
  await expect(changeRows(page)).toHaveCount(3);
  await expect(page.getByRole("button", { name: "Closed (3)" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "All (6)" })).toHaveAttribute("aria-pressed", "false");
});

test("the risk filter narrows the list", async ({ page }) => {
  await page.getByLabel("Risk").selectOption("high");
  await expect(page).toHaveURL(/risk=high/);
  await expect(changeRows(page)).toHaveCount(3);
});

test("the service filter narrows the list", async ({ page }) => {
  await page.getByLabel("Service").fill("iam");
  await expect(page).toHaveURL(/service=iam/);
  await expect(changeRows(page)).toHaveCount(3);
});

test("search and filters combine, and Clear filters resets them", async ({ page }) => {
  await page.getByLabel("Search").fill("demo app role");
  await expect(changeRows(page)).toHaveCount(3);
  await page.getByLabel("Risk").selectOption("high");
  await expect(changeRows(page)).toHaveCount(2);

  await page.getByLabel("Principal").fill("nobody-matches-this");
  await expect(page.getByText("No changes match these filters.")).toBeVisible();

  await page.getByRole("button", { name: "Clear filters" }).first().click();
  await expect(page).toHaveURL(/\/changes$/);
  await expect(changeRows(page)).toHaveCount(6);
});
