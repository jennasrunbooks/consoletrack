import type { Page } from "@playwright/test";
import { changeRows, expect, logIn, seedIntake, test } from "./fixtures";

// CHG-0042 is docs/mockups/change-details.png. The mock's revert follows openapi.yaml: 202, then
// `reverting` until the run finishes (MOCK_REVERT_MS, 8 s), which the page's 5 s poll picks up.

const card = (page: Page, heading: string) =>
  page.locator("section.card").filter({ has: page.getByRole("heading", { name: heading, exact: true }) });

async function openChange(page: Page, as: "admin" | "viewer", id = 42): Promise<void> {
  await logIn(page, as);
  const label = `CHG-${String(id).padStart(4, "0")}`;
  await changeRows(page).filter({ hasText: label }).locator("a.change-title").click();
  await expect(page).toHaveURL(new RegExp(`/changes/${id}$`));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test("opens from the change log and shows the mockup's content", async ({ page }) => {
  await openChange(page, "admin");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("SSH opened to the internet on the demo security group");
  await expect(card(page, "Agent summary")).toContainText("Confidence: high · 4 tool calls · claude-haiku-5-5 · 11 s");
  await expect(card(page, "Terraform expects vs. live").locator(".drift-added")).toContainText(
    "tcp 22 from 0.0.0.0/0 # live only, added out of band",
  );
  await expect(card(page, "What happened")).toContainText("7f3e2a10-9c4b-4d8e-b1a6-2c5d0e8f9a31");

  const evidence = card(page, "Evidence");
  await evidence.getByText(/^get_security_group\(/).click();
  await expect(evidence.getByLabel("Result of get_security_group")).toContainText('"cidrs": [\n        "0.0.0.0/0"');

  await page.getByRole("link", { name: "← Change log" }).click();
  await expect(page.getByRole("heading", { name: "Change log" })).toBeVisible();
});

test("Revert: confirm, run link, reverting, then reverted", async ({ page }) => {
  await openChange(page, "admin");
  await page.getByRole("button", { name: "Revert to Terraform" }).click();
  const dialog = page.getByRole("dialog", { name: "Revert CHG-0042 to Terraform?" });
  await expect(dialog).toBeVisible();
  // A modal dialog: Escape closes it and focus goes back to the button.
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "Revert to Terraform" })).toBeFocused();

  await page.getByRole("button", { name: "Revert to Terraform" }).click();
  await dialog.getByLabel("Note (optional)").fill("SSH stays closed");
  await dialog.getByRole("button", { name: "Start revert" }).click();
  await expect(page.getByRole("link", { name: "Open the workflow run" })).toHaveAttribute(
    "href",
    "https://github.com/jennasrunbooks/consoletrack/actions/runs/9042",
  );
  await expect(page.locator(".detail-tags .pill").nth(1)).toHaveText("Reverting");
  await expect(card(page, "Decision")).toContainText("SSH stays closed");
  await expect(page.locator(".detail-tags .pill").nth(1)).toHaveText("Reverted", { timeout: 20_000 });
  await expect(card(page, "Decision")).toContainText("Reverted to Terraform by admin");
  await expect(page.getByText("Refreshing every 5 s")).toBeHidden();
});

test("Accept needs a note", async ({ page }) => {
  await openChange(page, "admin", 40);
  const accept = page.getByRole("button", { name: "Accept with note" });
  await expect(accept).toBeDisabled();
  await page.getByLabel("Note (required to accept)").fill("Pipeline was down; the same change is in PR 812");
  await accept.click();
  await expect(page.locator(".detail-tags .pill").nth(1)).toHaveText("Accepted");
  await expect(card(page, "Decision")).toContainText("Pipeline was down; the same change is in PR 812");
});

test("replay mode disables Revert with 'AWS required'", async ({ page }) => {
  await seedIntake(page, { state: "replay" });
  await openChange(page, "admin");
  const revert = page.getByRole("button", { name: "Revert to Terraform" });
  await expect(revert).toHaveAttribute("aria-disabled", "true");
  await revert.hover();
  await expect(page.getByRole("tooltip", { name: "AWS required" })).toBeVisible();
  // aria-disabled keeps it focusable; Playwright counts it as disabled, so force the click.
  await revert.click({ force: true });
  await expect(page.getByRole("dialog")).toBeHidden();
});

test("viewers see every action disabled", async ({ page }) => {
  await openChange(page, "viewer");
  for (const name of ["Revert to Terraform", "Accept with note", "Codify as Terraform PR · Phase 2"]) {
    await expect(page.getByRole("button", { name })).toHaveAttribute("aria-disabled", "true");
  }
  await page.getByRole("button", { name: "Revert to Terraform" }).focus();
  await expect(page.getByRole("tooltip", { name: "Admin only" }).first()).toBeVisible();
});
