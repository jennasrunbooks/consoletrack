import { useState, type FormEvent } from "react";
import { ApiError } from "../api";
import { useAuth } from "../auth/AuthProvider";
import { Logo } from "../components/Logo";
import { ThemeToggle } from "../components/ThemeToggle";

function loginError(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Try again.";
  if (err.status === 401) return "Wrong username or password.";
  if (err.status === 429) {
    const minutes = err.retryAfter ? Math.max(1, Math.ceil(err.retryAfter / 60)) : 5;
    return `Too many failed attempts. Try again in ${minutes} min.`;
  }
  if (err.status === 0) return err.message;
  return "Couldn't log in. Try again.";
}

/** Shown in place of any page while logged out, so the URL (and its filters) survives login. */
export function LoginPage() {
  const { login, expired, error: sessionError, retry } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(loginError(err));
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-screen">
      <div className="login-theme">
        <ThemeToggle />
      </div>
      <main className="login-card">
        <Logo />
        <h1>Log in</h1>
        <p className="muted">Review the AWS changes made outside the pipeline.</p>
        {expired && !error && (
          <p className="notice" role="status">
            Your session ended. Log in again to continue.
          </p>
        )}
        {sessionError && !expired && !error && (
          <p className="notice" role="status">
            Couldn't check your session ({sessionError.message}).{" "}
            <button type="button" className="link-button" onClick={retry}>
              Retry
            </button>
          </p>
        )}
        <form onSubmit={onSubmit} noValidate>
          <label htmlFor="login-username">Username</label>
          <input
            id="login-username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
            maxLength={64}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "login-error" : undefined}
          />
          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            maxLength={256}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "login-error" : undefined}
          />
          {error && (
            <p id="login-error" className="form-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="button button-primary full" disabled={busy || !username.trim() || !password}>
            {busy ? "Logging in…" : "Log in"}
          </button>
        </form>
      </main>
    </div>
  );
}
