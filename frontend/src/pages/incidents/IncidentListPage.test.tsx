import { screen, waitFor, within } from "@testing-library/react";
import { http } from "msw";
import { errorResponse, mockDb } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { location, recordRequests, renderApp } from "../../test/render";

// Data rows only: the loading and empty states are a single row too, with no link.
const rows = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .filter((row) => within(row).queryAllByRole("link").length > 0);
const lastQuery = (seen: URL[]) => seen.at(-1)!.searchParams;

async function renderList(route = "/incidents") {
  const seen = recordRequests("/incidents");
  const result = renderApp(route);
  await screen.findByRole("heading", { name: "Incidents" });
  return { ...result, seen };
}

describe("incident list", () => {
  it("shows every incident newest first, linked to its detail page", async () => {
    await renderList();
    await waitFor(() => expect(rows()).toHaveLength(6));

    const first = within(rows()[0]!);
    expect(first.getByRole("link", { name: "KubePodNotReady" })).toHaveAttribute("href", "/incidents/15");
    expect(first.getByText("INC-15")).toBeInTheDocument();
    expect(first.getByText("Triaging")).toBeInTheDocument();
    expect(first.getByText("warning")).toBeInTheDocument();

    const oom = within(rows()[1]!);
    expect(oom.getByText("critical")).toBeInTheDocument();
    expect(oom.getByRole("link", { name: "DemoAppHighErrorRate" })).toHaveAttribute("href", "/incidents/14");
    expect(oom.getByText("demo-app")).toBeInTheDocument();
    expect(oom.getByText("demo")).toBeInTheDocument();
    expect(oom.getByText("9 min ago")).toBeInTheDocument();
    expect(oom.getByText("Open 9 min")).toBeInTheDocument();

    expect(within(rows()[2]!).getByRole("link", { name: /Linked to CHG-0041/ })).toHaveAttribute("href", "/changes/41");
    // Resolved 7 minutes after it opened.
    expect(within(rows()[4]!).getByText("7 min")).toBeInTheDocument();
    expect(within(rows()[5]!).getByText("Closed unresolved")).toBeInTheDocument();
    expect(screen.getByText(/Showing 6 of 6 incidents/)).toBeInTheDocument();
  });

  it("puts the status chip in the URL and the query", async () => {
    const { user, seen } = await renderList();
    await user.click(screen.getByRole("button", { name: "Active" }));

    expect(location.current).toBe("/incidents?status=open&status=triaging&status=awaiting_review&status=remediating");
    await waitFor(() =>
      expect(lastQuery(seen).getAll("status")).toEqual(["open", "triaging", "awaiting_review", "remediating"]),
    );
    await waitFor(() => expect(rows()).toHaveLength(4));
    expect(screen.getByRole("button", { name: "Active" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "Resolved" }));
    expect(location.current).toBe("/incidents?status=resolved");
    await waitFor(() => expect(rows()).toHaveLength(1));
  });

  it("puts the severity in the URL and the query, and Clear filters resets it", async () => {
    const { user, seen } = await renderList();
    await user.selectOptions(screen.getByLabelText("Severity"), "critical");
    expect(location.current).toBe("/incidents?severity=critical");
    await waitFor(() => expect(lastQuery(seen).get("severity")).toBe("critical"));
    await waitFor(() => expect(rows()).toHaveLength(3));

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(location.current).toBe("/incidents");
    await waitFor(() => expect(rows()).toHaveLength(6));
  });

  it("reads filters from the URL and drops values the API would reject", async () => {
    const { seen } = await renderList("/incidents?status=awaiting_review&status=bogus&severity=warning&page=0");
    await waitFor(() => expect(rows()).toHaveLength(1));
    const q = lastQuery(seen);
    expect(q.getAll("status")).toEqual(["awaiting_review"]);
    expect(q.get("severity")).toBe("warning");
    expect(q.get("page")).toBe("1");
    expect(screen.getByLabelText("Severity")).toHaveValue("warning");
    expect(screen.getByRole("button", { name: "Awaiting review" })).toHaveAttribute("aria-pressed", "true");
  });

  it("explains an empty list, with and without filters", async () => {
    mockDb.incidents = [];
    await renderList();
    expect(await screen.findByText(/No incidents yet/)).toBeInTheDocument();
  });

  it("says when nothing matches the filters", async () => {
    await renderList("/incidents?severity=info&status=resolved");
    expect(await screen.findByText("No incidents match these filters.")).toBeInTheDocument();
  });

  it("shows an error with Retry", async () => {
    let fail = true;
    server.use(
      http.get("*/api/v1/incidents", () => (fail ? errorResponse(500, "internal_error", "database unavailable") : undefined)),
    );
    const { user } = await renderList();
    expect(await screen.findByText("Couldn't load incidents: database unavailable")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(rows()).toHaveLength(6));
  });
});
