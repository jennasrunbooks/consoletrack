import { useState } from "react";
import { Link } from "react-router-dom";
import { TOUR_CHANGE, TOUR_INCIDENT } from "./links";

const KEY = "ct-demo-tour-dismissed";

function dismissedBefore(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

/** A light first-visit card with three places to look. Dismissal is remembered when storage works. */
export function DemoTour() {
  const [open, setOpen] = useState(() => !dismissedBefore());
  if (!open) return null;

  const dismiss = () => {
    setOpen(false);
    try {
      localStorage.setItem(KEY, "1");
    } catch {
      // Storage blocked: the card is back on the next visit.
    }
  };

  return (
    <section className="demo-tour" aria-labelledby="demo-tour-heading">
      <div className="demo-tour-head">
        <h2 id="demo-tour-heading">Take the 2-minute tour</h2>
        <button type="button" className="button button-secondary" onClick={dismiss}>
          Dismiss
        </button>
      </div>
      <ol>
        <li>
          <Link to={`/incidents/${TOUR_INCIDENT}`}>INC-{TOUR_INCIDENT}: an out-of-memory incident</Link> from a real recorded
          run. Read what the agent found, then Approve its fix and watch it apply and verify.
        </li>
        <li>
          <Link to={`/changes/${TOUR_CHANGE}`}>A console change waiting for review</Link>: SSH opened to the internet. See
          who made it and what it touched.
        </li>
        <li>
          <Link to={`/incidents/${TOUR_INCIDENT}`}>The postmortem editor</Link>: on INC-{TOUR_INCIDENT}, Edit postmortem,
          then Preview.
        </li>
      </ol>
    </section>
  );
}
