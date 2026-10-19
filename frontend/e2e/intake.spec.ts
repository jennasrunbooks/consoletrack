import type { IntakeHealth, IntakeState } from "../src/api";
import { expect, logIn, seedIntake, test } from "./fixtures";

// Built when this file loads, so a slow run can shift "2 min ago" by a minute or two.
const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

const cases: { state: IntakeState; seed: Partial<IntakeHealth>; label: RegExp; title: string; reason?: string }[] = [
  {
    state: "live",
    seed: { state: "live", last_event_at: minutesAgo(2) },
    label: /^Intake live · us-east-1 · last event \d+ min ago$/,
    title: "Change intake: Live",
  },
  {
    state: "delayed",
    seed: {
      state: "delayed",
      reasons: ["No canary event for 22 min"],
      last_event_at: minutesAgo(40),
      last_canary_at: minutesAgo(22),
    },
    label: /^Intake delayed · us-east-1 · last event \d+ min ago$/,
    title: "Change intake: Delayed",
    reason: "No canary event for 22 min",
  },
  {
    state: "down",
    seed: { state: "down", reasons: ["The trail isn't logging"], trail_logging: false, last_event_at: null },
    label: /^Intake down · us-east-1 · no events yet$/,
    title: "Change intake: Down",
    reason: "The trail isn't logging",
  },
  {
    state: "replay",
    seed: { state: "replay" },
    label: /^Replay mode · recorded events$/,
    title: "Change intake: Replay",
  },
];

for (const c of cases) {
  test(`shows the ${c.state} state`, async ({ page }) => {
    await seedIntake(page, c.seed);
    await logIn(page, "viewer");

    const button = page.locator(".intake-button");
    await expect(button).toHaveAttribute("data-state", c.state);
    await expect(button).toHaveText(c.label);
    await expect(button.locator(`.dot-${c.state}`)).toBeVisible();

    await button.click();
    const panel = page.getByRole("region", { name: "Change intake details" });
    await expect(panel.getByRole("heading")).toHaveText(c.title);
    if (c.reason) await expect(panel.getByRole("list", { name: "Why" })).toHaveText(c.reason);

    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(button).toBeFocused();
  });
}
