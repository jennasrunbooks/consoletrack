import { Logo } from "../components/Logo";
import { ThemeToggle } from "../components/ThemeToggle";
import { DemoBanner } from "./DemoBanner";

/** Where logging out lands: there is no login form in the demo, only a way back in. */
export function RestartDemoPage() {
  return (
    <>
      <DemoBanner />
      <div className="login-screen">
        <div className="login-theme">
          <ThemeToggle />
        </div>
        <main className="login-card">
          <Logo />
          <h1>Demo ended</h1>
          <p className="muted">You logged out of the recorded demo. Restart it to start over with fresh sample data.</p>
          <button type="button" className="button button-primary full" onClick={() => window.location.reload()}>
            Restart demo
          </button>
        </main>
      </div>
    </>
  );
}
