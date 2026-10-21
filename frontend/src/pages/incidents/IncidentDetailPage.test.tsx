import { act, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { mockDb } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { recordRequests, renderApp } from "../../test/render";

const card = (name: string) => screen.getByRole("heading", { name }).closest("section")!;

async function renderIncident(id: number, as: "admin" | "viewer" = "admin") {
  const result = renderApp(`/incidents/${id}`, { as });
  await screen.findByRole("heading", { level: 1 });
  return result;
}

describe("incident detail", () => {
  it("shows the alert, the analysis, the run, the timeline, and the proposed fix", async () => {
    await renderIncident(14);
    expect(screen.getByRole("heading", { level: 1, name: "DemoAppHighErrorRate" })).toBeInTheDocument();
    expect(screen.getByText("INC-14")).toBeInTheDocument();

    const analysis = within(card("Agent analysis"));
    expect(analysis.getByText(/OOMKilled \(exit code 137\) on each of its last 4 starts/)).toBeInTheDocument();
    expect(analysis.getByText(/The 128Mi memory limit is below what demo-app needs at startup/)).toBeInTheDocument();
    expect(analysis.getByText("Risk: high.")).toBeInTheDocument();
    expect(analysis.getByText(/Confidence: high · 6 tool calls/)).toBeInTheDocument();
    expect(analysis.getByText(/· prompt v2/)).toBeInTheDocument();
    expect(analysis.getByText("claude-haiku-5-5")).toBeInTheDocument();
    expect(analysis.queryByRole("list", { name: "Flags" })).toBeNull();

    const fix = within(card("Proposed fix"));
    expect(fix.getByText("set_memory_limit demo/demo-app container demo-app → 256Mi")).toBeInTheDocument();
    expect(fix.getByText(/128 MiB allocated \+ ~25 MiB baseline/)).toBeInTheDocument();
    expect(fix.getByRole("button", { name: "Approve fix" })).toBeEnabled();
    // The chart's policy is auto; with the kill switch off the gate leaves it to an admin.
    expect(fix.getByText("Manual")).toBeInTheDocument();
    expect(fix.getByText(/kill switch: AUTOFIX_ENABLED is false/)).toBeInTheDocument();

    const alert = within(card("Alert"));
    expect(alert.getByText("DemoAppHighErrorRate", { selector: "dd" })).toBeInTheDocument();
    expect(within(alert.getByRole("list", { name: "Labels" })).getAllByRole("listitem").map((l) => l.textContent)).toEqual([
      "alertname=DemoAppHighErrorRate",
      "namespace=demo",
      "severity=critical",
      "workload=demo-app",
    ]);
    expect(alert.getByText(/More than 5% of the traffic generator's requests/)).toBeInTheDocument();

    const steps = within(card("Status")).getAllByRole("listitem");
    expect(steps.map((s) => s.querySelector(".timeline-label")!.textContent)).toEqual([
      "Opened",
      "Triaging",
      "Awaiting review",
      "Remediating",
      "Resolved",
    ]);
    expect(steps[2]).toHaveAttribute("aria-current", "step");
    expect(steps[0]).toHaveClass("done");
    expect(steps[3]).toHaveClass("upcoming");
  });

  it("shows a low-confidence run with no proposal, and its flags as chips", async () => {
    await renderIncident(13);
    const flags = within(screen.getByRole("list", { name: "Flags" })).getAllByRole("listitem");
    expect(flags.map((f) => f.textContent)).toEqual([
      "Possible prompt injection",
      "AWS evidence unavailable",
      "Insufficient evidence",
    ]);
    expect(flags[0]).toHaveClass("flag-warning");
    expect(screen.getByText(/Confidence: low · 3 tool calls/)).toBeInTheDocument();
    expect(within(card("Proposed fix")).getByText(/No fix proposed: the agent's confidence is low/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve fix" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject and close" })).toBeNull();
  });

  it("renders evidence as text, collapsed until opened, never as HTML", async () => {
    const { user } = await renderIncident(13);
    const evidence = card("Evidence");
    const call = await within(evidence).findByText(
      'get_pod_logs(namespace="demo", pod="demo-app-loadgen-6d5f8b7c9-q4wz1", container="loadgen", previous=true, tail=50)',
    );
    const result = within(evidence).getByLabelText("Result of get_pod_logs");
    expect(result).not.toBeVisible();

    await user.click(call);
    expect(result).toBeVisible();
    expect(result.textContent).toContain("<script>alert(1)</script>");
    // k8s-mcp's JSON, pretty-printed as text.
    expect(result.textContent).toContain('\n  "line_count": 3,');
    expect(evidence.querySelector("script")).toBeNull();
  });

  it("disables Approve, Reject, and Re-run for viewers", async () => {
    await renderIncident(14, "viewer");
    for (const name of ["Approve fix", "Reject and close", "Re-run triage"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toHaveAttribute("aria-disabled", "true");
      expect(button).toHaveAccessibleDescription("Admin only");
    }
    expect(screen.getByLabelText("Reason (required to reject)")).toBeDisabled();
    expect(screen.getByLabelText("Model (optional)")).toBeDisabled();
  });

  it("approves a fix: the incident moves to remediating and the approval is the first step", async () => {
    const { user } = await renderIncident(14);
    await user.click(screen.getByRole("button", { name: "Approve fix" }));
    expect(await screen.findByText(/Fix approved/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("Remediating", { selector: ".detail-tags .pill" })).toBeInTheDocument(),
    );
    const steps = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(steps[0]).toHaveTextContent("approved");
    expect(steps[0]).toHaveTextContent("approved by admin");
    expect(screen.queryByRole("button", { name: "Approve fix" })).toBeNull();
    // An approved fix is still being applied, so the page keeps polling.
    expect(screen.getByText("Refreshing every 5 s")).toBeInTheDocument();
  });

  it("can't approve a kind the backend can't apply yet, and shows a 501 as not available", async () => {
    const r = mockDb.incidents.find((i) => i.id === 14)!.remediation!;
    Object.assign(r, { kind: "rollback", payload: { kind: "rollback", namespace: "demo", deployment: "demo-app", to_revision: null } });
    await renderIncident(14);
    expect(screen.getByRole("button", { name: "Approve fix" })).toBeDisabled();
    expect(screen.getByText(/only set_memory_limit fixes can be applied/)).toBeInTheDocument();
  });

  it("explains a 501 from the backend", async () => {
    server.use(
      http.post("*/api/v1/incidents/14/remediation/approve", () =>
        HttpResponse.json(
          { code: "not_implemented", message: "approving a rollback remediation is not available yet" },
          { status: 501 },
        ),
      ),
    );
    const { user } = await renderIncident(14);
    await user.click(screen.getByRole("button", { name: "Approve fix" }));
    expect(
      await screen.findByText("Not available yet: approving a rollback remediation is not available yet. Nothing was changed."),
    ).toBeInTheDocument();
  });

  it("shows the conflict and reloads when someone else decided first", async () => {
    server.use(
      http.post("*/api/v1/incidents/14/remediation/approve", () =>
        HttpResponse.json(
          { code: "no_proposed_remediation", message: "incident 14 has no proposed remediation to decide on" },
          { status: 409 },
        ),
      ),
    );
    const { user } = await renderIncident(14);
    await user.click(screen.getByRole("button", { name: "Approve fix" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("incident 14 has no proposed remediation to decide on");
  });

  it("shows a fix the policy gate applied: mode, policy, the values it changed, and every step", async () => {
    await renderIncident(11);
    const fix = within(card("Proposed fix"));
    expect(fix.getByText("Auto")).toBeInTheDocument();
    expect(fix.getByText("Succeeded")).toBeInTheDocument();
    expect(fix.getByText(/approved by policy:oom-memory-bump/)).toBeInTheDocument();
    expect(fix.getByText("limits.memory 128Mi → 256Mi")).toBeInTheDocument();
    const steps = within(fix.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(steps.map((li) => li.querySelector(".apply-step-name")!.textContent)).toEqual([
      "claimed",
      "read",
      "validate",
      "dry_run",
      "patch",
      "rollout",
      "verify",
      "succeeded",
    ]);
    expect(steps[6]).toHaveTextContent("the alert resolved");
    expect(screen.queryByText("Refreshing every 5 s")).toBeNull();
  });

  it("requires a reason to reject, and sends it", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/v1/incidents/14/remediation/reject", async ({ request }) => {
        bodies.push(await request.clone().json());
      }),
    );
    const { user } = await renderIncident(14);
    const reject = screen.getByRole("button", { name: "Reject and close" });
    expect(reject).toBeDisabled();
    await user.type(screen.getByLabelText("Reason (required to reject)"), "   ");
    expect(reject).toBeDisabled();

    await user.type(screen.getByLabelText("Reason (required to reject)"), "wrong container");
    await user.click(reject);
    expect(await screen.findByText("Fix rejected; incident closed.")).toBeInTheDocument();
    expect(bodies).toEqual([{ reason: "wrong container" }]);
    await waitFor(() => expect(screen.getByText("Closed", { selector: ".detail-tags .pill" })).toBeInTheDocument());
    expect(screen.getByText(/rejected by admin: wrong container/)).toBeInTheDocument();
  });

  it("re-runs triage with a model override and shows the run in progress", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/v1/incidents/13/triage", async ({ request }) => {
        bodies.push(await request.clone().json());
      }),
    );
    const { user } = await renderIncident(13);
    await user.type(screen.getByLabelText("Model (optional)"), "claude-sonnet-5-5");
    await user.click(screen.getByRole("button", { name: "Re-run triage" }));

    expect(await screen.findByText(/Re-running triage/)).toBeInTheDocument();
    expect(bodies).toEqual([{ model: "claude-sonnet-5-5" }]);
    expect(screen.getByText("Triaging", { selector: ".detail-tags .pill" })).toBeInTheDocument();
    expect(screen.getByText("Refreshing every 5 s")).toBeInTheDocument();
  });

  it("shows the backend's message for a model it doesn't allow", async () => {
    const { user } = await renderIncident(13);
    await user.type(screen.getByLabelText("Model (optional)"), "gpt-9");
    await user.click(screen.getByRole("button", { name: "Re-run triage" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("model must be one of: claude-haiku-5-5, claude-sonnet-5-5");
  });

  it("sends a null model when the field is empty", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/v1/incidents/14/triage", async ({ request }) => {
        bodies.push(await request.clone().json());
      }),
    );
    const { user } = await renderIncident(14);
    await user.click(screen.getByRole("button", { name: "Re-run triage" }));
    await waitFor(() => expect(bodies).toEqual([{ model: null }]));
  });

  it("can't re-run triage while remediating", async () => {
    await renderIncident(12);
    expect(screen.getByRole("button", { name: "Re-run triage" })).toBeDisabled();
    expect(screen.getByText(/Available when the incident is open or awaiting review, not remediating/)).toBeInTheDocument();
    const fix = within(card("Proposed fix"));
    expect(fix.getByText("revert_workflow CHG-0041")).toBeInTheDocument();
    expect(fix.getByRole("link", { name: "Workflow run" })).toHaveAttribute(
      "href",
      "https://github.com/jennasrunbooks/consoletrack/actions/runs/1",
    );
    expect(within(card("Linked changes")).getByRole("link", { name: /CHG-0041/ })).toHaveAttribute("href", "/changes/41");
  });

  it("doesn't link an external_url that isn't http(s)", async () => {
    mockDb.incidents.find((i) => i.id === 12)!.remediation!.external_url = "javascript:alert(1)";
    await renderIncident(12);
    expect(within(card("Proposed fix")).queryByRole("link")).toBeNull();
  });

  it.each(["999", "abc"])("shows not found for incident %s", async (id) => {
    renderApp(`/incidents/${id}`);
    expect(await screen.findByRole("heading", { name: "Incident not found" })).toBeInTheDocument();
  });

  it("shows an error with Retry", async () => {
    let fail = true;
    server.use(
      http.get("*/api/v1/incidents/14", () =>
        fail ? HttpResponse.json({ code: "internal_error", message: "database unavailable" }, { status: 500 }) : undefined,
      ),
    );
    const { user } = renderApp("/incidents/14");
    expect(await screen.findByText("Couldn't load this incident: database unavailable")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: "DemoAppHighErrorRate" })).toBeInTheDocument();
  });

  describe("polling", () => {
    beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
    afterEach(() => vi.useRealTimers());

    it("polls every 5 s while triaging, shows the analysis without a reload, then stops", async () => {
      mockDb.triage[15] = { doneAt: Date.now() + 7_000, model: "claude-haiku-5-5" };
      const seen = recordRequests("/incidents/15");
      renderApp("/incidents/15");
      expect(await screen.findByText(/The agent is triaging this incident/)).toBeInTheDocument();
      expect(screen.getByText("Refreshing every 5 s")).toBeInTheDocument();
      const initial = seen.length;

      await act(() => vi.advanceTimersByTimeAsync(5_000));
      await waitFor(() => expect(seen.length).toBe(initial + 1));
      expect(screen.getByText(/The agent is triaging this incident/)).toBeInTheDocument();

      await act(() => vi.advanceTimersByTimeAsync(5_000));
      expect(await screen.findByText(/probes call \/not-a-health-check/)).toBeInTheDocument();
      expect(screen.getByText("Awaiting review", { selector: ".detail-tags .pill" })).toBeInTheDocument();
      expect(screen.queryByText("Refreshing every 5 s")).toBeNull();

      const settled = seen.length;
      await act(() => vi.advanceTimersByTimeAsync(20_000));
      expect(seen.length).toBe(settled);
    });

    it("doesn't poll an incident that's already past triage", async () => {
      const seen = recordRequests("/incidents/11");
      renderApp("/incidents/11");
      await screen.findByRole("heading", { name: "DemoAppHighErrorRate" });
      const initial = seen.length;
      await act(() => vi.advanceTimersByTimeAsync(20_000));
      expect(seen.length).toBe(initial);
    });
  });
});
