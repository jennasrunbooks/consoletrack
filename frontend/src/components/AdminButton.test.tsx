import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import type { User } from "../api";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { mockUsers } from "../mocks/fixtures";
import { mockSession } from "../mocks/handlers";
import { AdminButton } from "./AdminButton";

async function renderAs(role: "admin" | "viewer") {
  mockSession.user = mockUsers[role]!.user as User;
  const onClick = vi.fn();
  render(
    <AuthProvider>
      <Gate>
        <AdminButton onClick={onClick}>Revert</AdminButton>
      </Gate>
    </AuthProvider>,
  );
  return { onClick, button: await screen.findByRole("button", { name: "Revert" }) };
}

// AdminButton lives behind the login gate; mirror that here.
function Gate({ children }: { children: ReactNode }) {
  return useAuth().user ? <>{children}</> : null;
}

describe("AdminButton", () => {
  it("works for an admin", async () => {
    const { button, onClick } = await renderAs("admin");
    expect(button).toBeEnabled();
    expect(button).not.toHaveAttribute("aria-disabled");
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("is disabled for a viewer, says why, and stays focusable", async () => {
    const { button, onClick } = await renderAs("viewer");
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAccessibleDescription("Admin only");
    expect(screen.getByRole("tooltip", { hidden: true })).toHaveTextContent("Admin only");
    await userEvent.tab();
    expect(button).toHaveFocus();
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});
