import { Link, useParams } from "react-router-dom";
import { changeLabel } from "../lib/format";

// Change detail ships in #13 PR 2b; this keeps links from the change log working.

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

export function NotFoundPage() {
  return (
    <section className="placeholder">
      <h1>Page not found</h1>
      <Link to="/changes">← Back to the change log</Link>
    </section>
  );
}
