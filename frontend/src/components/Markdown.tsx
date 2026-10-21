import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

// Postmortems are the agent's draft (built from pod logs and CloudTrail text) edited by people,
// so they are untrusted. react-markdown builds React elements, never an HTML string; raw HTML in
// the source is dropped (skipHtml) and the tree goes through rehype-sanitize's GitHub-style
// allowlist as well. Images are left out: they would load from anywhere the text points.
const schema = {
  ...defaultSchema,
  tagNames: (defaultSchema.tagNames ?? []).filter((tag) => tag !== "img"),
};

// The Markdown sits under a card's <h2>, so its headings start at <h3>: `#` → h3, `##` → h4, and
// so on, keeping one <h1> per page and the outline intact.
const shifted = (Tag: "h3" | "h4" | "h5" | "h6"): Components["h1"] =>
  function Heading({ children }) {
    return <Tag>{children}</Tag>;
  };

const components: Components = {
  h1: shifted("h3"),
  h2: shifted("h4"),
  h3: shifted("h5"),
  h4: shifted("h6"),
  h5: shifted("h6"),
  h6: shifted("h6"),
  a: ({ href, title, children }) => (
    <a href={href} title={title} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
};

export function Markdown({ source, label }: { source: string; label?: string }) {
  return (
    <div className="markdown" role={label ? "region" : undefined} aria-label={label}>
      <ReactMarkdown skipHtml rehypePlugins={[[rehypeSanitize, schema]]} components={components}>
        {source}
      </ReactMarkdown>
    </div>
  );
}
