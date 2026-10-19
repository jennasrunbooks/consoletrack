import { useId, type ButtonHTMLAttributes } from "react";
import { useUser } from "../auth/AuthProvider";

/**
 * A button for an admin-only action (every write in openapi.yaml). Viewers see it disabled with
 * an "Admin only" tooltip; the backend enforces the same rule with 403.
 */
export function AdminButton({ children, disabled, className, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  const { role } = useUser();
  const tipId = useId();
  if (role === "admin") {
    return (
      <button type="button" className={className} disabled={disabled} {...props}>
        {children}
      </button>
    );
  }
  // A disabled button gets no hover or focus events, so the wrapper carries the tooltip and
  // aria-disabled keeps the button focusable for keyboard and screen-reader users.
  return (
    <span className="tip-anchor">
      <button
        type="button"
        className={className}
        aria-disabled="true"
        aria-describedby={tipId}
        {...props}
        onClick={(e) => e.preventDefault()}
      >
        {children}
      </button>
      <span role="tooltip" id={tipId} className="tip">
        Admin only
      </span>
    </span>
  );
}
