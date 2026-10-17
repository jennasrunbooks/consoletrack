import { useEffect, useId, useRef, useState } from "react";
import { useAuth, useUser } from "../auth/AuthProvider";

export function UserMenu() {
  const user = useUser();
  const { logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const onLogout = async () => {
    setError(null);
    try {
      await logout();
    } catch {
      setError("Couldn't log out. Try again.");
    }
  };

  return (
    <div className="user-menu" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="icon-button user-button"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={`Account: ${user.username}`}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="avatar" aria-hidden="true">
          {user.username.slice(0, 1).toUpperCase()}
        </span>
      </button>
      {open && (
        <div id={menuId} className="menu-panel" role="region" aria-label="Account">
          <p className="menu-user">
            Signed in as <strong>{user.username}</strong>
          </p>
          <p className="muted small">
            {user.role === "admin" ? "Admin: can revert, accept, and approve." : "Viewer: read-only. Actions need an admin."}
          </p>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button type="button" className="button button-secondary full" onClick={onLogout}>
            Log out
          </button>
        </div>
      )}
    </div>
  );
}
