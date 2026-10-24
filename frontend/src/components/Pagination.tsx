interface PageInfo {
  items: unknown[];
  total: number;
  page: number;
  page_size: number;
}

/** "Showing 1–25 of 30 changes" and Previous/Next, for any paginated list response. */
export function Pagination({
  page,
  onPage,
  noun,
  sample = false,
}: {
  page: PageInfo;
  onPage: (p: number) => void;
  /** Singular and plural, e.g. ["change", "changes"]. */
  noun: [string, string];
  /** Adds "sample data from the demo scenarios" (npm run dev:mock). */
  sample?: boolean;
}) {
  const pages = Math.max(1, Math.ceil(page.total / page.page_size));
  const first = (page.page - 1) * page.page_size + 1;
  const last = first + page.items.length - 1;
  const range =
    page.items.length === 0 ? `0 of ${page.total}` : pages === 1 ? `${page.items.length} of ${page.total}` : `${first}–${last} of ${page.total}`;
  return (
    <nav className="pagination" aria-label="Pagination">
      <p className="muted">
        Showing {range} {page.total === 1 ? noun[0] : noun[1]}
        {sample && " · sample data from the demo scenarios"}
      </p>
      {pages > 1 && (
        <div className="pager">
          <button
            type="button"
            className="button button-secondary"
            disabled={page.page <= 1}
            onClick={() => onPage(Math.min(page.page - 1, pages))}
          >
            Previous
          </button>
          <span aria-current="page">
            Page {Math.min(page.page, pages)} of {pages}
          </span>
          <button
            type="button"
            className="button button-secondary"
            disabled={page.page >= pages}
            onClick={() => onPage(page.page + 1)}
          >
            Next
          </button>
        </div>
      )}
    </nav>
  );
}
