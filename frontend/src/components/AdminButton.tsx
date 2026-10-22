import { useId, type ButtonHTMLAttributes } from "react";
import { useUser } from "../auth/AuthProvider";

interface AdminButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Disables the button for admins too, with this as its tooltip (e.g. "AWS required"). */
  disabledReason?: string | null;
}

/**
 * A button for an admin-only action (every write in openapi.yaml). Viewers see it disabled with
 * an "Admin only" tooltip; the backend enforces the same rule with 403.
 */
export function AdminButton({
  children,
  disabled,
  disabledReason,
  className,
  "aria-describedby": describedBy,
  ...props
}: AdminButtonProps) {
  const { role } = useUser();
  const tipId = useId();
  const reason = role !== "admin" ? "Admin only" : disabledReason;
  if (!reason) {
    return (
      <button type="button" className={className} disabled={disabled} aria-describedby={describedBy} {...props}>
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
        aria-describedby={describedBy ? `${tipId} ${describedBy}` : tipId}
        {...props}
        onClick={(e) => e.preventDefault()}
      >
        {children}
      </button>
      <span role="tooltip" id={tipId} className="tip">
        {reason}
      </span>
    </span>
  );
}
