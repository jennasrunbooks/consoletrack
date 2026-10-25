import { act, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { buildIntakeHealth } from "../../mocks/fixtures";
import { errorResponse, mockDb, MOCK_REVERT_MS } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { recordRequests, renderApp } from "../../test/render";

const card = (name: string) => screen.getByRole("heading", { name }).closest("section")!;

async function renderChange(id: number, as: "admin" | "viewer" = "admin") {
  const result = renderApp(`/changes/${id}`, { as });
  await screen.findByRole("heading", { level: 1 });
  return result;
}

/** Records the JSON body of every request to `path` (then lets the normal handler answer). */
function recordBodies(path: string): unknown[] {
  const bodies: unknown[] = [];
  server.use(
    http.all(`*/api/v1${path}`, async ({ request }) => {
      bodies.push(await request.clone().json().catch(() => null));
    }),
  );
  return bodies;
}

describe("change detail", () => {
  // CHG-0042 is docs/mockups/change-details.png.
  it("shows the summary, the drift, what happened, and the evidence, as in the mockup", async () => {
    await renderChange(42);
    expect(screen.getByRole("heading", { level: 1, name: "SSH opened to the internet on the demo security group" }))
      .toBeInTheDocument();
    expect(screen.getByText("High risk")).toHaveClass("risk-high");
    expect(screen.getByText("Awaiting review", { selector: ".detail-tags .pill" })).toBeInTheDocument();
    expect(screen.getByText("CHG-0042")).toBeInTheDocument();
    expect(document.querySelector(".lede")).toHaveTextContent(/^Detected \d+ min ago · made in the AWS console · not in Terraform$/);

    const summary = within(card("Agent summary"));
    expect(summary.getByText(/added an inbound rule to demo-app-sg allowing TCP 22 from 0.0.0.0\/0/)).toBeInTheDocument();
    expect(summary.getByText("Risk: high.")).toBeInTheDocument();
    expect(summary.getByText(/Confidence: high · 4 tool calls/)).toBeInTheDocument();
    expect(summary.getByText(/· 11 s/)).toBeInTheDocument();

    const drift = within(card("Terraform expects vs. live"));
    expect(drift.getByText("aws_security_group.demo_app")).toBeInTheDocument();
    const lines = drift.getAllByRole("listitem");
    expect(lines.map((l) => l.textContent)).toEqual([
      " In both: tcp 443 from [ALLOWED_CIDRS] # in Terraform and live",
      "+Live only: tcp 22 from 0.0.0.0/0 # live only, added out of band",
    ]);
    expect(lines[1]).toHaveClass("drift-added");

    const facts = within(card("What happened"));
    expect(facts.getByText("assumed-role/ct-demo-developer/jdev")).toBeInTheDocument();
    expect(facts.getByText("AWS console")).toBeInTheDocument();
    expect(facts.getByText("AuthorizeSecurityGroupIngress")).toBeInTheDocument();
    expect(facts.getByText("203.0.113.x", { exact: false })).toBeInTheDocument();
    expect(facts.queryByText(/203\.0\.113\.24/)).not.toBeInTheDocument();
    expect(facts.getByText("sg-0a1b2c3d4e5f60718 (demo-app-sg)")).toBeInTheDocument();
    expect(facts.getByText("terraform/demo-app/us-east-1 · aws_security_group.demo_app")).toBeInTheDocument();
    expect(facts.getByText("7f3e2a10-9c4b-4d8e-b1a6-2c5d0e8f9a31")).toBeInTheDocument();
    expect(facts.getByText(/UTC$/)).toBeInTheDocument();

    expect(within(card("Linked incidents")).getByText(/None yet/)).toBeInTheDocument();
    const evidence = within(card("Evidence"));
    expect(await evidence.findByText(/^get_resource_changes\(resource_id="sg-0a1b2c3d4e5f60718", hours=24\)$/))
      .toBeInTheDocument();
    expect(evidence.getAllByRole("listitem")).toHaveLength(4);
  });

  it("links the incidents a change caused", async () => {
    await renderChange(41);
    const linked = within(card("Linked incidents"));
    expect(linked.getByRole("link", { name: "INC-12: demo-app DemoAppHighErrorRate" })).toHaveAttribute(
      "href",
      "/incidents/12",
    );
    expect(linked.getByText("Remediating · high confidence")).toBeInTheDocument();
    expect(within(card("Terraform expects vs. live")).getAllByRole("listitem")[1]).toHaveClass("drift-removed");
  });

  describe("Accept", () => {
    it("requires a note, then closes the change with it", async () => {
      const bodies = recordBodies("/changes/42/accept");
      const { user } = await renderChange(42);
      const decision = within(card("Decision"));
      const accept = decision.getByRole("button", { name: "Accept with note" });
      expect(accept).toBeDisabled();

      await user.type(decision.getByLabelText("Note (required to accept)"), "   ");
      expect(accept).toBeDisabled();
      await user.clear(decision.getByLabelText("Note (required to accept)"));
      await user.type(decision.getByLabelText("Note (required to accept)"), "Bastion test, closing it tonight");
      expect(accept).toBeEnabled();
      await user.click(accept);

      expect(await screen.findByText("Change accepted. The note is its record.")).toBeInTheDocument();
      expect(bodies).toEqual([{ note: "Bastion test, closing it tonight" }]);
      await waitFor(() => expect(screen.getByText("Accepted", { selector: ".detail-tags .pill" })).toBeInTheDocument());
      expect(screen.getByText("Change accepted. The note is its record.")).toBeInTheDocument();
      expect(within(card("Decision")).getByText("Bastion test, closing it tonight")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Revert to Terraform" })).toBeNull();
    });

    it("shows a conflict and reloads when someone decided first", async () => {
      server.use(
        http.post("*/api/v1/changes/42/accept", () => {
          mockDb.changes.find((c) => c.id === 42)!.status = "reverting";
          return errorResponse(409, "invalid_state_transition", "accepted is not allowed from reverting");
        }),
      );
      const { user } = await renderChange(42);
      await user.type(screen.getByLabelText("Note (required to accept)"), "fine");
      await user.click(screen.getByRole("button", { name: "Accept with note" }));
      expect(await screen.findByText("accepted is not allowed from reverting")).toBeInTheDocument();
      await waitFor(() => expect(screen.getByText("Reverting", { selector: ".detail-tags .pill" })).toBeInTheDocument());
    });
  });

  describe("Revert", () => {
    it("confirms, then shows the run link and the reverting state", async () => {
      const bodies = recordBodies("/changes/42/revert");
      const { user } = await renderChange(42);
      await user.click(screen.getByRole("button", { name: "Revert to Terraform" }));

      const dialog = screen.getByRole("dialog", { name: "Revert CHG-0042 to Terraform?" });
      expect(within(dialog).getByText(/waits in the/)).toBeInTheDocument();
      expect(within(dialog).getByText("terraform/demo-app/us-east-1")).toBeInTheDocument();
      expect(bodies).toEqual([]);
      await user.type(within(dialog).getByLabelText("Note (optional)"), "SSH stays closed");
      await user.click(within(dialog).getByRole("button", { name: "Start revert" }));

      const link = await screen.findByRole("link", { name: "Open the workflow run" });
      expect(link).toHaveAttribute("href", "https://github.com/jennasrunbooks/consoletrack/actions/runs/9042");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
      expect(bodies).toEqual([{ note: "SSH stays closed" }]);
      expect(screen.queryByRole("dialog")).toBeNull();
      await waitFor(() => expect(screen.getByText("Reverting", { selector: ".detail-tags .pill" })).toBeInTheDocument());
      const decision = within(card("Decision"));
      expect(decision.getByText("Reverting to Terraform.")).toBeInTheDocument();
      // The result outlives the review actions, which unmount once the change is reverting.
      expect(decision.getByRole("link", { name: "Open the workflow run" })).toBeInTheDocument();
      expect(decision.getByRole("link", { name: "Workflow run" })).toHaveAttribute(
        "href",
        "https://github.com/jennasrunbooks/consoletrack/actions/runs/9042",
      );
      expect(screen.getByText("Refreshing every 5 s")).toBeInTheDocument();
    });

    it("sends nothing when the dialog is cancelled", async () => {
      const bodies = recordBodies("/changes/42/revert");
      const { user } = await renderChange(42);
      await user.click(screen.getByRole("button", { name: "Revert to Terraform" }));
      await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(bodies).toEqual([]);
      expect(screen.getByRole("button", { name: "Revert to Terraform" })).toHaveFocus();
    });

    // The backend answers 501 until the gated revert (#14) lands.
    it("says clearly that revert isn't available yet when the backend answers 501", async () => {
      server.use(
        http.post("*/api/v1/changes/42/revert", () =>
          errorResponse(
            501,
            "not_implemented",
            "POST /api/v1/changes/{id}/revert is not implemented yet; it ships with #14 (gated Revert)",
          ),
        ),
      );
      const { user } = await renderChange(42);
      await user.click(screen.getByRole("button", { name: "Revert to Terraform" }));
      await user.click(screen.getByRole("button", { name: "Start revert" }));

      const notice = await screen.findByText("Revert isn't available yet.");
      expect(notice.closest("p")).toHaveClass("notice-warning");
      expect(notice.closest("p")).toHaveTextContent(/501 Not Implemented .*ships with #14.*Nothing was dispatched/);
      expect(screen.getByText("Awaiting review", { selector: ".detail-tags .pill" })).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Open the workflow run" })).toBeNull();
    });

    it("is disabled with 'AWS required' in replay mode", async () => {
      mockDb.intake = buildIntakeHealth({ state: "replay" });
      const { user } = await renderChange(42);
      const revert = screen.getByRole("button", { name: "Revert to Terraform" });
      await waitFor(() => expect(revert).toHaveAttribute("aria-disabled", "true"));
      expect(revert).toHaveAccessibleDescription(/^AWS required/);
      expect(screen.getByText(/Replay mode has no AWS account to revert in/)).toBeInTheDocument();
      await user.click(revert);
      expect(screen.queryByRole("dialog")).toBeNull();
      // Accept doesn't need AWS.
      expect(screen.getByRole("button", { name: "Accept with note" })).not.toHaveAttribute("aria-disabled");
    });

    it("waits for the agent's summary on a detected change, but Accept is allowed", async () => {
      mockDb.changes.find((c) => c.id === 42)!.status = "detected";
      mockDb.changeDetails[42]!.analysis = null;
      await renderChange(42);
      expect(screen.getByText(/The agent is looking into this change/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Revert to Terraform" })).toHaveAccessibleDescription(
        /^Available once the agent has summarized the change/,
      );
      expect(screen.getByLabelText("Note (required to accept)")).toBeEnabled();
    });

    it("shows a failed run and lets the change be reviewed again", async () => {
      mockDb.changes.find((c) => c.id === 41)!.status = "failed";
      Object.assign(mockDb.changeDetails[41]!.remediation!, { status: "failed", error: "terraform apply: AccessDenied" });
      await renderChange(41);
      const decision = within(card("Decision"));
      expect(decision.getByRole("alert")).toHaveTextContent("The revert run failed. terraform apply: AccessDenied");
      expect(decision.getByRole("button", { name: "Revert to Terraform" })).toBeEnabled();
      expect(decision.getByRole("button", { name: "Accept with note" })).toBeInTheDocument();
    });
  });

  it("shows Codify disabled as Phase 2", async () => {
    await renderChange(42);
    const codify = screen.getByRole("button", { name: "Codify as Terraform PR · Phase 2" });
    expect(codify).toHaveAttribute("aria-disabled", "true");
    expect(codify).toHaveAccessibleDescription("Codify ships in Phase 2");
  });

  it("shows the decision on a closed change, with no actions", async () => {
    await renderChange(39);
    const decision = within(card("Decision"));
    expect(decision.getByText("Accepted")).toBeInTheDocument();
    expect(decision.getByText("FinOps tagging; codify the tag in the next demo-app pull request.")).toBeInTheDocument();
    expect(decision.queryByRole("button")).toBeNull();
    expect(within(card("Terraform expects vs. live")).getByText(/No drift recorded yet/)).toBeInTheDocument();
  });

  it("masks the source IP until an admin clicks Show", async () => {
    const { user } = await renderChange(42);
    const facts = within(card("What happened"));
    expect(facts.queryByText(/203\.0\.113\.24/)).not.toBeInTheDocument();
    await user.click(facts.getByRole("button", { name: "Show the full source IP" }));
    expect(facts.getByText("203.0.113.24")).toBeInTheDocument();
  });

  it("never offers viewers the full source IP", async () => {
    await renderChange(42, "viewer");
    const facts = within(card("What happened"));
    expect(facts.getByText("203.0.113.x")).toBeInTheDocument();
    expect(facts.queryByRole("button", { name: "Show the full source IP" })).not.toBeInTheDocument();
  });

  it("gives viewers every action disabled with 'Admin only'", async () => {
    await renderChange(42, "viewer");
    for (const name of ["Revert to Terraform", "Accept with note", "Codify as Terraform PR · Phase 2"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toHaveAttribute("aria-disabled", "true");
      expect(button).toHaveAccessibleDescription(/^Admin only/);
    }
    expect(screen.getByLabelText("Note (required to accept)")).toBeDisabled();
  });

  it("renders evidence, request parameters, and the user agent as text, never as HTML", async () => {
    const evil = '<script>alert("x")</script><img src=x onerror=alert(1)>';
    Object.assign(mockDb.changeDetails[42]!, { user_agent: evil, request_parameters: { groupId: evil } });
    mockDb.changeEvidence[42]![0]!.result_excerpt = evil;
    const { user } = await renderChange(42);

    const evidence = card("Evidence");
    await user.click(await within(evidence).findByText(/^get_resource_changes\(/));
    expect(within(evidence).getByLabelText("Result of get_resource_changes").textContent).toBe(evil);
    const facts = card("What happened");
    expect(within(facts).getByText(evil)).toBeInTheDocument();
    await user.click(within(facts).getByText("Request parameters", { selector: "summary" }));
    expect(within(facts).getByLabelText("Request parameters").textContent).toContain('"groupId": "<script>');
    expect(document.querySelector("article script, article img")).toBeNull();
  });

  it("says when a change doesn't exist", async () => {
    renderApp("/changes/999");
    expect(await screen.findByRole("heading", { name: "Change not found" })).toBeInTheDocument();
    expect(screen.getByText("There's no change CHG-0999.")).toBeInTheDocument();
  });

  it("rejects an id that isn't a number without calling the API", async () => {
    const seen = recordRequests("/changes/*");
    renderApp("/changes/abc");
    expect(await screen.findByRole("heading", { name: "Change not found" })).toBeInTheDocument();
    expect(seen).toEqual([]);
  });

  it("shows an error with Retry", async () => {
    let fail = true;
    server.use(
      http.get("*/api/v1/changes/42", () =>
        fail ? errorResponse(500, "internal_error", "Database unavailable") : undefined,
      ),
    );
    const { user } = renderApp("/changes/42");
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load this change: Database unavailable");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { level: 1, name: /SSH opened/ })).toBeInTheDocument();
  });

  it("shows a loading state first", async () => {
    renderApp("/changes/42");
    expect(await screen.findByText("Loading CHG-0042…")).toHaveAttribute("role", "status");
    await screen.findByRole("heading", { level: 1 });
  });

  it("shows an empty evidence list and an evidence error with Retry", async () => {
    mockDb.changeEvidence[42] = [];
    await renderChange(42);
    expect(await within(card("Evidence")).findByText("No tool calls recorded yet.")).toBeInTheDocument();

    server.use(http.get("*/api/v1/changes/40/evidence", () => HttpResponse.error()));
    renderApp("/changes/40");
    expect(await screen.findByText(/Couldn't load the evidence/)).toBeInTheDocument();
  });

  describe("polling", () => {
    beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
    afterEach(() => vi.useRealTimers());

    it("polls while reverting until the run finishes, then stops", async () => {
      const seen = recordRequests("/changes/42");
      const { user } = await renderChange(42);
      await user.click(screen.getByRole("button", { name: "Revert to Terraform" }));
      await user.click(screen.getByRole("button", { name: "Start revert" }));
      await screen.findByText("Reverting to Terraform.");

      await act(() => vi.advanceTimersByTimeAsync(MOCK_REVERT_MS + 5_000));
      await waitFor(() => expect(screen.getByText("Reverted", { selector: ".detail-tags .pill" })).toBeInTheDocument());
      expect(within(card("Decision")).getByText("Reverted to Terraform")).toBeInTheDocument();
      expect(screen.queryByText("Refreshing every 5 s")).toBeNull();

      const settled = seen.length;
      await act(() => vi.advanceTimersByTimeAsync(20_000));
      expect(seen.length).toBe(settled);
    });

    it("doesn't poll a change that's awaiting review", async () => {
      const seen = recordRequests("/changes/42");
      await renderChange(42);
      const initial = seen.length;
      await act(() => vi.advanceTimersByTimeAsync(20_000));
      expect(seen.length).toBe(initial);
    });
  });
});
