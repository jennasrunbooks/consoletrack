import type { Page } from "@playwright/test";
import { changeRows, expect, logIn, test } from "./fixtures";

// The six sample incidents in src/mocks/fixtures.ts. INC-15 is triaging and its run finishes
// 6 s after the page loads (MOCK_TRIAGE_MS), so the page's 5 s poll picks it up on the 2nd tick.

async function openIncidents(page: Page, as: "admin" | "viewer", path = "/incidents"): Promise<void> {
  await logIn(page, as);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Incidents" }).click();
  await expect(page.getByRole("heading", { name: "Incidents", level: 1 })).toBeVisible();
  if (path !== "/incidents") await page.goto(path);
}

const card = (page: Page, heading: string) =>
  page.locator("section.card").filter({ has: page.getByRole("heading", { name: heading, exact: true }) });

test("the list filters by status and severity through the URL", async ({ page }) => {
  await openIncidents(page, "viewer");
  await expect(changeRows(page)).toHaveCount(6);

  await page.getByRole("button", { name: "Active" }).click();
  await expect(page).toHaveURL(/status=open&status=triaging&status=awaiting_review&status=remediating/);
  await expect(changeRows(page)).toHaveCount(4);

  await page.getByLabel("Severity").selectOption("critical");
  // Active and critical: INC-14 and INC-12 (INC-11 is resolved).
  await expect(page).toHaveURL(/severity=critical/);
  await expect(changeRows(page)).toHaveCount(2);

  await page.reload();
  await expect(changeRows(page)).toHaveCount(2);
  await expect(page.getByLabel("Severity")).toHaveValue("critical");

  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page).toHaveURL(/\/incidents$/);
  await expect(changeRows(page)).toHaveCount(6);

  await page.getByRole("link", { name: "DemoAppHighErrorRate" }).first().click();
  await expect(page).toHaveURL(/\/incidents\/14$/);
});

test("detail shows the analysis and the proposed fix, and Approve runs it", async ({ page }) => {
  await openIncidents(page, "admin", "/incidents/14");
  await expect(page.getByRole("heading", { level: 1, name: "DemoAppHighErrorRate" })).toBeVisible();
  const analysis = card(page, "Agent analysis");
  await expect(analysis).toContainText("is in CrashLoopBackOff with 3 restarts");
  await expect(analysis).toContainText("Confidence: medium · 7 tool calls · claude-haiku-5-5");
  const fix = card(page, "Proposed fix");
  await expect(fix).toContainText("set_memory_limit demo/demo-app container demo-app → 256Mi");
  await expect(page.locator(".timeline-step.current")).toContainText("Awaiting review");

  await expect(fix).toContainText("the agent's confidence is medium; policy oom-memory-bump needs high");

  // Approve: the apply loop runs the fix and the page polls until its steps are in.
  await page.getByRole("button", { name: "Approve fix" }).click();
  await expect(page.locator(".detail-tags")).toContainText("Remediating");
  const steps = fix.getByRole("list", { name: "Steps" });
  await expect(steps.getByRole("listitem").first()).toContainText("approved by admin");
  await expect(steps.getByRole("listitem").last()).toContainText("succeeded", { timeout: 15_000 });
  await expect(fix.locator(".remediation-result")).toHaveText("Fixed: memory limit 128Mi → 256Mi");
});

test("a low-confidence run with no proposal shows its flags and untrusted evidence as text", async ({ page }) => {
  await openIncidents(page, "admin", "/incidents/13");
  const flags = page.getByRole("list", { name: "Flags" });
  await expect(flags.getByRole("listitem")).toHaveText([
    "Possible prompt injection",
    "AWS evidence unavailable",
    "Insufficient evidence",
  ]);
  await expect(card(page, "Proposed fix")).toContainText("No fix proposed: the agent's confidence is low");
  await expect(page.getByRole("button", { name: "Approve fix" })).toHaveCount(0);

  const result = page.getByLabel("Result of get_pod_logs");
  await expect(result).toBeHidden();
  await page.getByText(/^get_pod_logs\(/).click();
  await expect(result).toContainText("<script>alert(1)</script>");
  await expect(card(page, "Evidence").locator("script")).toHaveCount(0);
});

test("a viewer sees the buttons disabled with Admin only", async ({ page }) => {
  await openIncidents(page, "viewer", "/incidents/14");
  for (const name of ["Approve fix", "Reject and close", "Re-run triage"]) {
    const button = page.getByRole("button", { name });
    await expect(button).toHaveAttribute("aria-disabled", "true");
  }
  await page.getByRole("button", { name: "Approve fix" }).hover();
  await expect(page.getByRole("tooltip", { name: "Admin only" }).first()).toBeVisible();
});

test("a triaging incident polls until the analysis arrives, then stops", async ({ page }) => {
  const polls: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/v1/incidents/15") polls.push(request.url());
  });
  await openIncidents(page, "admin", "/incidents/15");
  await expect(card(page, "Agent analysis")).toContainText("The agent is triaging this incident");
  await expect(page.getByText("Refreshing every 5 s")).toBeVisible();

  await expect(card(page, "Agent analysis")).toContainText("probes call /not-a-health-check", { timeout: 15_000 });
  await expect(page.locator(".detail-tags")).toContainText("Awaiting review");
  await expect(page.getByText("Refreshing every 5 s")).toHaveCount(0);

  const settled = polls.length;
  expect(settled, "the first load plus at least one poll").toBeGreaterThanOrEqual(2);
  await page.waitForTimeout(6_000);
  expect(polls.length).toBe(settled);
});
