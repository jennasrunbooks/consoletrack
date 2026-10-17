import { NavLink, Outlet } from "react-router-dom";
import { IntakeIndicator } from "./IntakeIndicator";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";

export function AppShell() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="app-header">
        <NavLink to="/changes" className="logo-link" aria-label="ConsoleTrack home">
          <Logo />
        </NavLink>
        <nav className="app-nav" aria-label="Main">
          <NavLink to="/changes">Changes</NavLink>
          <NavLink to="/incidents">Incidents</NavLink>
        </nav>
        <div className="header-actions">
          <IntakeIndicator />
          <ThemeToggle />
          <UserMenu />
        </div>
      </header>
      <main id="main" className="page" tabIndex={-1}>
        <Outlet />
      </main>
    </>
  );
}
