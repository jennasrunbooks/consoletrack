import { safeHttpUrl } from "../lib/format";
import { HOW_IT_WORKS_URL, REPO_URL } from "./links";

const RESET_TIP = "Everything here is sample data held in your browser. Reload the page to reset the demo.";

/** Not dismissible: every page of the demo says it is a recording. */
export function DemoBanner() {
  const video = safeHttpUrl(import.meta.env.VITE_DEMO_VIDEO_URL);
  return (
    <aside className="demo-banner" aria-label="About this demo">
      <p className="demo-banner-label" title={RESET_TIP}>
        <strong>Recorded demo</strong> · sample data, no live AWS
        <span className="visually-hidden">. {RESET_TIP}</span>
      </p>
      <ul className="demo-banner-links">
        {video && (
          <li>
            <a href={video} target="_blank" rel="noopener noreferrer">
              Watch the 2-min video
            </a>
          </li>
        )}
        <li>
          <a href={HOW_IT_WORKS_URL} target="_blank" rel="noopener noreferrer">
            How it works
          </a>
        </li>
        <li>
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
            Source
          </a>
        </li>
      </ul>
    </aside>
  );
}
