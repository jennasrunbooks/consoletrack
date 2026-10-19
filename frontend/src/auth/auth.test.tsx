import { screen, waitFor, within } from "@testing-library/react";
import { http } from "msw";
import { errorResponse, mockSession } from "../mocks/handlers";
import { server } from "../mocks/server";
import { location, renderApp } from "../test/render";

describe("login", () => {
  it("shows the login page when GET /auth/me says there is no session", async () => {
    renderApp("/changes", { as: null });
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("logs in and lands on the URL it started from, filters included", async () => {
    const { user } = renderApp("/changes?status=reverting", { as: null });
    await user.type(await screen.findByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "admin");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByRole("heading", { name: "Change log" })).toBeInTheDocument();
    expect(location.current).toBe("/changes?status=reverting");
    expect(await screen.findByRole("button", { name: /^Reverting/, pressed: true })).toBeInTheDocument();
  });

  it("says so on a wrong password and clears the password field", async () => {
    const { user } = renderApp("/", { as: null });
    await user.type(await screen.findByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong username or password.");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Username")).toHaveAttribute("aria-invalid", "true");
  });

  it("explains the lockout on 429 using Retry-After", async () => {
    server.use(
      http.post("*/api/v1/auth/login", () =>
        errorResponse(429, "too_many_requests", "too many", { headers: { "Retry-After": "170" } }),
      ),
    );
    const { user } = renderApp("/", { as: null });
    await user.type(await screen.findByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "admin");
    await user.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many failed attempts. Try again in 3 min.");
  });

  it("logs out from the user menu", async () => {
    const { user } = renderApp("/changes");
    await user.click(await screen.findByRole("button", { name: "Account: admin" }));
    await user.click(screen.getByRole("button", { name: "Log out" }));
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(mockSession.user).toBeNull();
  });

  it("returns to the login page with a note when the session expires", async () => {
    const { user } = renderApp("/changes");
    await screen.findByRole("heading", { name: "Change log" });
    mockSession.user = null; // the cookie ran out on the server
    await user.click(screen.getByRole("button", { name: /^Closed/ }));
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session ended.");
  });
});

describe("viewer", () => {
  it("shows the role in the user menu", async () => {
    const { user } = renderApp("/changes", { as: "viewer" });
    await user.click(await screen.findByRole("button", { name: "Account: viewer" }));
    const menu = screen.getByRole("region", { name: "Account" });
    expect(within(menu).getByText(/Viewer: read-only/)).toBeInTheDocument();
  });

  it("can read the change log", async () => {
    renderApp("/changes", { as: "viewer" });
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(7));
  });
});
