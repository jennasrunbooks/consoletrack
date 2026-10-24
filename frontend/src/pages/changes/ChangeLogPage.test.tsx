import { screen, waitFor, within } from "@testing-library/react";
import { http } from "msw";
import { buildChanges } from "../../mocks/fixtures";
import { errorResponse, mockDb } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { location, recordRequests, renderApp } from "../../test/render";

const rows = () => screen.getAllByRole("row").slice(1); // minus the header row
const lastQuery = (seen: URL[]) => seen.at(-1)!.searchParams;

async function renderLog(route = "/changes") {
  const seen = recordRequests("/changes");
  const result = renderApp(route);
  await screen.findByRole("link", { name: "SSH opened to the internet on the demo security group" }).catch(() => {});
  return { ...result, seen };
}

describe("change log", () => {
  it("shows the tiles, chips, and rows from GET /changes", async () => {
    await renderLog();
    const tiles = screen.getByRole("list", { name: "Summary" });
    expect(within(tiles).getAllByRole("listitem").map((t) => t.textContent)).toEqual([
      "Awaiting review2",
      "High risk, still open2",
      "Linked to an incident1",
      "Closed this week3",
    ]);
    const chips = within(screen.getByRole("group", { name: "Status" })).getAllByRole("button");
    expect(chips.map((c) => c.textContent)).toEqual(["All (6)", "Awaiting review (2)", "Reverting (1)", "Closed (3)"]);
    expect(chips[0]).toHaveAttribute("aria-pressed", "true");

    expect(rows()).toHaveLength(6);
    const first = within(rows()[0]!);
    expect(first.getByRole("link", { name: "SSH opened to the internet on the demo security group" })).toHaveAttribute(
      "href",
      "/changes/42",
    );
    expect(first.getByText("CHG-0042")).toBeInTheDocument();
    expect(first.getByText("aws_security_group.demo_app")).toBeInTheDocument();
    expect(first.getByText("ct-demo-developer")).toBeInTheDocument();
    expect(first.getByText("console")).toBeInTheDocument();
    expect(first.getByText("High")).toBeInTheDocument();
    expect(first.getByText("Awaiting review")).toBeInTheDocument();
    expect(first.getByText("4 min ago")).toBeInTheDocument();

    expect(within(rows()[1]!).getByRole("link", { name: "Linked to INC-12: demo-app DemoAppHighErrorRate" })).toHaveAttribute(
      "href",
      "/incidents/12",
    );
    expect(within(rows()[2]!).getByText("terraform, outside pipeline")).toBeInTheDocument();
    expect(within(rows()[2]!).getByText("AdministratorAccess (SSO)")).toBeInTheDocument();
    expect(screen.getByText(/Showing 6 of 6 changes/)).toBeInTheDocument();
  });

  it("puts the status chip in the URL and the query; Closed sends reverted, accepted, codified", async () => {
    const { user, seen } = await renderLog();
    await user.click(screen.getByRole("button", { name: /^Closed/ }));

    expect(location.current).toBe("/changes?status=reverted&status=accepted&status=codified");
    await waitFor(() => expect(lastQuery(seen).getAll("status")).toEqual(["reverted", "accepted", "codified"]));
    await waitFor(() => expect(rows()).toHaveLength(3));
    expect(screen.getByRole("button", { name: "Closed (3)" })).toHaveAttribute("aria-pressed", "true");
    // counts ignore the status filter, so the other chips keep their numbers
    expect(screen.getByRole("button", { name: "All (6)" })).toHaveAttribute("aria-pressed", "false");

    await user.click(screen.getByRole("button", { name: "All (6)" }));
    expect(location.current).toBe("/changes");
  });

  it("puts risk, principal, service, and dates in the URL and the query", async () => {
    const { user, seen } = await renderLog();
    await user.selectOptions(screen.getByLabelText("Risk"), "high");
    await user.type(screen.getByLabelText("Principal"), "jdev");
    await user.type(screen.getByLabelText("Service"), "IAM");
    await user.type(screen.getByLabelText("From"), "2026-10-01");
    await user.type(screen.getByLabelText("To"), "2026-10-17");

    await waitFor(() =>
      expect(Object.fromEntries(new URL(location.current, "http://x").searchParams)).toEqual({
        risk: "high",
        principal: "jdev",
        service: "iam",
        since: "2026-10-01",
        until: "2026-10-17",
      }),
    );
    await waitFor(() => {
      const q = lastQuery(seen);
      expect(q.get("risk")).toBe("high");
      expect(q.get("principal")).toBe("jdev");
      expect(q.get("service")).toBe("iam");
      expect(q.get("since")).toBe(new Date(2026, 9, 1).toISOString());
      expect(q.get("until")).toBe(new Date(2026, 9, 18).toISOString());
    });
  });

  it("debounces typing into one request", async () => {
    const { user, seen } = await renderLog();
    const before = seen.length;
    await user.type(screen.getByLabelText("Search"), "bucket");
    await waitFor(() => expect(lastQuery(seen).get("q")).toBe("bucket"));
    expect(seen.length - before).toBe(1);
    await waitFor(() => expect(rows()).toHaveLength(1));
  });

  it("doesn't send a service name the API would reject", async () => {
    const { user } = await renderLog();
    await user.type(screen.getByLabelText("Service"), "ec2!");
    expect(await screen.findByText("Lowercase letters, digits, and dashes only")).toBeInTheDocument();
    expect(location.current).toBe("/changes");
  });

  it("reads filters from the URL on load and fills the controls", async () => {
    const { seen } = await renderLog("/changes?status=awaiting_review&risk=medium&q=trust&page=1");
    await waitFor(() => expect(rows()).toHaveLength(1));
    const q = lastQuery(seen);
    expect(q.getAll("status")).toEqual(["awaiting_review"]);
    expect(q.get("risk")).toBe("medium");
    expect(q.get("q")).toBe("trust");
    expect(screen.getByLabelText("Risk")).toHaveValue("medium");
    expect(screen.getByLabelText("Search")).toHaveValue("trust");
    expect(screen.getByRole("button", { name: /^Awaiting review/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("clears every filter", async () => {
    const { user } = await renderLog("/changes?risk=low&principal=nobody");
    expect(await screen.findByText("No changes match these filters.")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Clear filters" })[0]!);
    expect(location.current).toBe("/changes");
    await waitFor(() => expect(rows()).toHaveLength(6));
    expect(screen.getByLabelText("Principal")).toHaveValue("");
  });

  it("pages through results and resets to page 1 when a filter changes", async () => {
    const base = buildChanges();
    mockDb.changes = Array.from({ length: 30 }, (_, i) => ({ ...base[i % base.length]!, id: 100 + i }));
    const { user, seen } = await renderLog();
    await waitFor(() => expect(rows()).toHaveLength(25));
    expect(screen.getByText(/Showing 1–25 of 30 changes/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(location.current).toBe("/changes?page=2");
    await waitFor(() => expect(rows()).toHaveLength(5));
    expect(lastQuery(seen).get("page")).toBe("2");
    expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("Risk"), "high");
    expect(location.current).toBe("/changes?risk=high");
  });

  it("explains an empty log without filters", async () => {
    mockDb.changes = [];
    await renderLog();
    expect(await screen.findByText(/No out-of-band changes yet/)).toBeInTheDocument();
  });

  it("shows an error with Retry", async () => {
    let fail = true;
    server.use(
      http.get("*/api/v1/changes", () => (fail ? errorResponse(500, "internal_error", "database unavailable") : undefined)),
    );
    const { user } = renderApp("/changes");
    expect(await screen.findByText("Couldn't load changes: database unavailable")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(rows()).toHaveLength(6));
  });

  it("renders untrusted text as text, never HTML", async () => {
    const evil = '<script>window.__pwned = true</script><img src=x onerror="window.__pwned = true">';
    mockDb.changes = [{ ...buildChanges()[0]!, title: evil, resource_name: evil, session_name: evil }];
    await renderLog();
    expect(await screen.findByRole("link", { name: evil })).toBeInTheDocument();
    expect(document.querySelector("tbody script, tbody img")).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
  });

  it("shows Pending for a change the agent hasn't assessed", async () => {
    mockDb.changes = [{ ...buildChanges()[0]!, risk: null, status: "detected", tf_address: null }];
    await renderLog();
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(within(rows()[0]!).getByText("Pending")).toBeInTheDocument();
    expect(within(rows()[0]!).getByText("no Terraform address")).toBeInTheDocument();
    // optional chips appear once they have something to show
    expect(screen.getByRole("button", { name: "Detected (1)" })).toBeInTheDocument();
  });
});
