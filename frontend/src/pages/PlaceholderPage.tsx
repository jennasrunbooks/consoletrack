import { Link, useParams } from "react-router-dom";
import { changeLabel } from "../lib/format";

// Change detail and incidents ship in PR 2 (#13); these keep links from the change log working.

export function ChangeDetailPlaceholder() {
  const { id } = useParams();
  const n = Number(id);
  return (
    <section className="placeholder">
      <h1>{Number.isInteger(n) && n > 0 ? changeLabel(n) : "Change"}</h1>
      <p className="muted">The change detail page is coming next: summary, analysis, evidence, and actions.</p>
      <Link to="/changes">← Back to the change log</Link>
    </section>
  );
}

export function IncidentsPlaceholder() {
  return (
    <section className="placeholder">
      <h1>Incidents</h1>
      <p className="muted">The incident list and detail pages are coming next.</p>
      <Link to="/changes">← Back to the change log</Link>
    </section>
  );
}

export function NotFoundPage() {
  return (
    <section className="placeholder">
      <h1>Page not found</h1>
      <Link to="/changes">← Back to the change log</Link>
    </section>
  );
}
