import { act, screen, within } from "@testing-library/react";
import { http } from "msw";
import type { IntakeHealth } from "../api";
import { buildIntakeHealth } from "../mocks/fixtures";
import { errorResponse, mockDb } from "../mocks/handlers";
import { server } from "../mocks/server";
import { recordRequests, renderApp } from "../test/render";

const indicator = () => screen.findByRole("button", { name: /^(Intake|Replay)/ });

async function renderWith(overrides: Partial<IntakeHealth>) {
  mockDb.intake = buildIntakeHealth(overrides);
  const result = renderApp("/changes");
  return { ...result, button: await indicator() };
}

describe("intake indicator", () => {
  it.each([
    ["live", "Intake live · us-east-1 · last event 2 min ago"],
    ["delayed", "Intake delayed · us-east-1 · last event 2 min ago"],
    ["down", "Intake down · us-east-1 · last event 2 min ago"],
    ["replay", "Replay mode · recorded events"],
  ] as const)("shows %s", async (state, label) => {
    const { button } = await renderWith({ state });
    expect(button).toHaveTextContent(label);
    expect(button).toHaveAttribute("data-state", state);
    expect(button.querySelector(`.dot-${state}`)).not.toBeNull();
  });

  it("explains itself on hover and focus", async () => {
    const { button } = await renderWith({});
    expect(button).toHaveAccessibleDescription("Shows whether AWS change events are reaching ConsoleTrack");
  });

  it("opens a panel with the health details, the key, and the Grafana link", async () => {
    const { user, button } = await renderWith({});
    expect(button).toHaveAttribute("aria-expanded", "false");
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");

    const panel = screen.getByRole("region", { name: "Change intake details" });
    expect(within(panel).getByRole("heading")).toHaveTextContent("Change intake: Live");
    const details = Object.fromEntries(
      within(panel)
        .getAllByRole("term")
        .map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]),
    );
    expect(details).toEqual({
      Watching: "us-east-1 + IAM (global)",
      "Last event": "2 min ago",
      "Last canary": "6 min ago · every 15 min",
      Queue: "0 waiting · oldest 0 s",
      "Dead-letter": "0",
      Trail: "logging",
    });
    const key = within(panel).getByRole("list", { name: "Indicator key" });
    expect(within(key).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Live",
      "Delayed",
      "Down",
      "Replay",
    ]);
    const link = within(panel).getByRole("link", { name: "Open intake dashboard in Grafana →" });
    expect(link).toHaveAttribute("href", "http://localhost:3000/d/consoletrack-intake/intake-health");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("lists the reasons when intake is down, and unknowns as unknown", async () => {
    const { user, button } = await renderWith({
      state: "down",
      reasons: ["no canary for 52 min", "trail is not logging"],
      queue_backlog: null,
      oldest_message_age_seconds: null,
      dead_letter_count: 3,
      trail_logging: false,
      grafana_url: null,
    });
    await user.click(button);
    const panel = screen.getByRole("region", { name: "Change intake details" });
    expect(within(panel).getByRole("list", { name: "Why" })).toHaveTextContent("no canary for 52 min");
    expect(within(panel).getByText("unknown · unknown")).toBeInTheDocument();
    expect(within(panel).getByText("not logging")).toBeInTheDocument();
    expect(within(panel).queryByRole("link")).not.toBeInTheDocument();
  });

  it("refuses a non-http Grafana URL", async () => {
    const { user, button } = await renderWith({ grafana_url: "javascript:alert(1)" });
    await user.click(button);
    expect(screen.queryByRole("link", { name: /Grafana/ })).not.toBeInTheDocument();
  });

  it("closes on Escape and returns focus to the button", async () => {
    const { user, button } = await renderWith({});
    await user.click(button);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Change intake details" })).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });

  it("shows 'unavailable' rather than guessing when the health call fails", async () => {
    server.use(http.get("*/api/v1/intake/health", () => errorResponse(500, "internal_error", "boom")));
    renderApp("/changes");
    expect(await screen.findByRole("button", { name: "Intake status unavailable" })).toHaveAttribute(
      "data-state",
      "unknown",
    );
  });

  it("polls every 30 s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const seen = recordRequests("/intake/health");
      const { button } = await renderWith({});
      expect(seen).toHaveLength(1);

      mockDb.intake = buildIntakeHealth({ state: "delayed" });
      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(seen).toHaveLength(2);
      expect(button).toHaveAttribute("data-state", "delayed");
    } finally {
      vi.useRealTimers();
    }
  });
});
