import { Logo } from "../components/Logo";
import { ThemeToggle } from "../components/ThemeToggle";
import { DemoBanner } from "./DemoBanner";

/**
 * Shown when an /api request got past the mock service worker (a hard reload skips it, or the
 * browser blocks workers). The demo never calls a backend, so the page asks for a reload instead.
 */
export function DemoErrorPage() {
  return (
    <>
      <DemoBanner />
      <div className="login-screen">
        <div className="login-theme">
          <ThemeToggle />
        </div>
        <main className="login-card">
          <Logo />
          <h1>Reload the demo</h1>
          <p className="muted">
            The sample data is served by a service worker in your browser, and it isn't running on this page. Reload to start
            it. If this keeps happening, check that your browser allows service workers for this site.
          </p>
          <button type="button" className="button button-primary full" onClick={() => window.location.reload()}>
            Reload the demo
          </button>
        </main>
      </div>
    </>
  );
}
