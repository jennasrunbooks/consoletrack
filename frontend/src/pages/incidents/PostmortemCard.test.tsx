import { screen, waitFor, within } from "@testing-library/react";
import { http } from "msw";
import { errorResponse, mockDb } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { renderApp } from "../../test/render";

const postmortemCard = () => screen.getByRole("heading", { name: "Postmortem" }).closest("section")!;

async function renderIncident(id: number, as: "admin" | "viewer" = "admin") {
  const result = renderApp(`/incidents/${id}`, { as });
  await screen.findByRole("link", { name: "← Incidents" });
  return result;
}

function recordBodies(path: string): unknown[] {
  const bodies: unknown[] = [];
  server.use(
    http.all(`*/api/v1${path}`, async ({ request }) => {
      bodies.push(await request.clone().json().catch(() => null));
    }),
  );
  return bodies;
}

// Blank lines between the cases: in CommonMark an HTML block runs until the next blank line.
const XSS = [
  "# Impact",
  "<script>alert(1)</script>",
  '<img src="https://attacker.example/pixel.png" onerror="alert(2)">',
  "![tracker](https://attacker.example/t.png)",
  "[click me](javascript:alert(3))",
  '<a href="https://example.com" onclick="alert(4)">raw link</a>',
  "[runbook](https://example.com/runbook)",
].join("\n\n");

describe("postmortem editor", () => {
  // INC-14's analysis carries the agent's real draft (src/mocks/recorded-oom.ts).
  it("renders the agent's draft as Markdown", async () => {
    await renderIncident(14);
    const card = within(postmortemCard());
    const preview = card.getByRole("region", { name: "Postmortem" });
    expect(within(preview).getByRole("heading", { level: 4, name: "Root cause" })).toBeInTheDocument();
    expect(within(preview).getByText("demo", { selector: "code" })).toBeInTheDocument();
    expect(within(preview).getAllByRole("list").length).toBeGreaterThan(0);
    expect(card.getByRole("button", { name: "Edit postmortem" })).toBeEnabled();
  });

  it("edits, previews, and saves the Markdown", async () => {
    const bodies = recordBodies("/incidents/14/postmortem");
    const { user } = await renderIncident(14);
    const card = within(postmortemCard());
    await user.click(card.getByRole("button", { name: "Edit postmortem" }));

    const source = card.getByLabelText("Postmortem (Markdown)");
    expect((source as HTMLTextAreaElement).value).toMatch(/^## Summary\n/);
    await user.clear(source);
    await user.type(source, "## Impact{Enter}{Enter}Every request **failed**.");
    expect(card.getByText(/36 \/ 100,000 characters/)).toBeInTheDocument();

    await user.click(card.getByRole("tab", { name: "Preview" }));
    expect(card.getByRole("tab", { name: "Preview" })).toHaveAttribute("aria-selected", "true");
    const panel = card.getByRole("tabpanel", { name: "Preview" });
    expect(within(panel).getByRole("heading", { level: 4, name: "Impact" })).toBeInTheDocument();
    expect(within(panel).getByText("failed", { selector: "strong" })).toBeInTheDocument();

    await user.click(card.getByRole("button", { name: "Save postmortem" }));
    expect(await screen.findByText("Postmortem saved.")).toBeInTheDocument();
    expect(bodies).toEqual([{ postmortem_md: "## Impact\n\nEvery request **failed**." }]);
    await waitFor(() =>
      expect(within(postmortemCard()).getByRole("region", { name: "Postmortem" })).toHaveTextContent(
        /^Impact\s*Every request failed\.$/,
      ),
    );
    expect(mockDb.incidents.find((i) => i.id === 14)!.analysis!.postmortem_md).toBe("## Impact\n\nEvery request **failed**.");
  });

  it("moves between Write and Preview with the arrow keys", async () => {
    const { user } = await renderIncident(14);
    await user.click(screen.getByRole("button", { name: "Edit postmortem" }));
    const write = screen.getByRole("tab", { name: "Write" });
    write.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Preview" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("Postmortem (Markdown)").closest("[role=tabpanel]")).not.toBeVisible();
    await user.keyboard("{Home}");
    expect(write).toHaveFocus();
    expect(write).toHaveAttribute("tabindex", "0");
  });

  it("sanitizes the preview: no raw HTML, scripts, images, or javascript: links", async () => {
    mockDb.incidents.find((i) => i.id === 14)!.analysis!.postmortem_md = XSS;
    const { user } = await renderIncident(14);
    const card = postmortemCard();

    const check = (root: Element) => {
      expect(root.querySelector("script, img, [onerror], [onclick]")).toBeNull();
      for (const a of root.querySelectorAll("a")) expect(a.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
      expect(within(root as HTMLElement).getByRole("link", { name: "runbook" })).toHaveAttribute(
        "href",
        "https://example.com/runbook",
      );
    };
    check(card);
    // `# Impact` becomes an <h3> under the card's <h2>: still one <h1> on the page.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(within(card).getByRole("heading", { level: 3, name: "Impact" })).toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "Edit postmortem" }));
    await user.click(within(card).getByRole("tab", { name: "Preview" }));
    check(within(card).getByRole("tabpanel", { name: "Preview" }));
    expect(document.querySelector("script")).toBeNull();
  });

  it("cancels without saving", async () => {
    const bodies = recordBodies("/incidents/14/postmortem");
    const { user } = await renderIncident(14);
    await user.click(screen.getByRole("button", { name: "Edit postmortem" }));
    await user.type(screen.getByLabelText("Postmortem (Markdown)"), "draft");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Postmortem (Markdown)")).toBeNull();
    expect(bodies).toEqual([]);
  });

  it("keeps the draft and shows the error when saving fails", async () => {
    server.use(
      http.put("*/api/v1/incidents/14/postmortem", () =>
        errorResponse(501, "not_implemented", "PUT /api/v1/incidents/{id}/postmortem is not implemented yet"),
      ),
    );
    const { user } = await renderIncident(14);
    await user.click(screen.getByRole("button", { name: "Edit postmortem" }));
    await user.type(screen.getByLabelText("Postmortem (Markdown)"), " more");
    await user.click(screen.getByRole("button", { name: "Save postmortem" }));
    expect(await screen.findByText(/^Not available yet: PUT .* Nothing was changed\.$/)).toBeInTheDocument();
    expect((screen.getByLabelText("Postmortem (Markdown)") as HTMLTextAreaElement).value).toMatch(/ more$/);
  });

  it("won't save past the length limit", async () => {
    mockDb.incidents.find((i) => i.id === 14)!.analysis!.postmortem_md = "x".repeat(100_001);
    const { user } = await renderIncident(14);
    await user.click(screen.getByRole("button", { name: "Edit postmortem" }));
    expect(screen.getByLabelText("Postmortem (Markdown)")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Save postmortem" })).toBeDisabled();
  });

  it("explains an incident with no analysis yet, and one with no postmortem", async () => {
    await renderIncident(15);
    expect(within(postmortemCard()).getByText(/drafts a postmortem with its analysis/)).toBeInTheDocument();
    expect(within(postmortemCard()).queryByRole("button")).toBeNull();
  });

  it("offers to write one when the analysis has no draft", async () => {
    await renderIncident(13);
    expect(within(postmortemCard()).getByText(/No postmortem yet/)).toBeInTheDocument();
    expect(within(postmortemCard()).getByRole("button", { name: "Write postmortem" })).toBeEnabled();
  });

  it("is read-only for viewers", async () => {
    await renderIncident(14, "viewer");
    const edit = within(postmortemCard()).getByRole("button", { name: "Edit postmortem" });
    expect(edit).toHaveAttribute("aria-disabled", "true");
    expect(edit).toHaveAccessibleDescription("Admin only");
  });
});
