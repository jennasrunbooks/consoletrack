import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section className="placeholder">
      <h1>Page not found</h1>
      <Link to="/changes">← Back to the change log</Link>
    </section>
  );
}
