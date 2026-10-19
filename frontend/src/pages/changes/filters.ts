// Change log filters live in the URL, so a filtered view can be shared and survives reloads and
// the login screen. This maps the page's URL to the GET /changes query and back.
import type { ChangeCounts, ChangeStatus, ListChangesQuery, Risk } from "../../api";

export const PAGE_SIZE = 25;

const STATUSES: ChangeStatus[] = [
  "detected",
  "awaiting_review",
  "reverting",
  "reverted",
  "accepted",
  "codifying",
  "codified",
  "failed",
];
const RISKS: Risk[] = ["low", "medium", "high"];
const DATE = /^\d{4}-\d{2}-\d{2}$/;
export const SERVICE_PATTERN = /^[a-z0-9-]+$/;

export interface Filters {
  status: ChangeStatus[];
  risk: Risk[];
  principal: string;
  service: string;
  /** Inclusive start day, YYYY-MM-DD in the viewer's time zone. */
  since: string;
  /** Inclusive end day, YYYY-MM-DD in the viewer's time zone. */
  until: string;
  q: string;
  page: number;
}

/** Parses the URL leniently: unknown values are dropped rather than sent to the API. */
export function parseFilters(params: URLSearchParams): Filters {
  const page = Number.parseInt(params.get("page") ?? "1", 10);
  const service = (params.get("service") ?? "").trim().toLowerCase();
  const day = (name: string) => {
    const v = params.get(name) ?? "";
    return DATE.test(v) && !Number.isNaN(Date.parse(v)) ? v : "";
  };
  return {
    status: unique(params.getAll("status").filter((s): s is ChangeStatus => STATUSES.includes(s as ChangeStatus))),
    risk: unique(params.getAll("risk").filter((r): r is Risk => RISKS.includes(r as Risk))),
    principal: (params.get("principal") ?? "").trim(),
    service: SERVICE_PATTERN.test(service) ? service : "",
    since: day("since"),
    until: day("until"),
    q: (params.get("q") ?? "").trim(),
    page: Number.isInteger(page) && page > 0 ? page : 1,
  };
}

/** Local midnight of a YYYY-MM-DD day, as an ISO timestamp; `addDays` moves it. */
function localMidnight(day: string, addDays = 0): string {
  const [y, m, d] = day.split("-").map(Number) as [number, number, number];
  return new Date(y, m - 1, d + addDays).toISOString();
}

export function toQuery(f: Filters): ListChangesQuery {
  const query: ListChangesQuery = { page: f.page, page_size: PAGE_SIZE };
  if (f.status.length) query.status = f.status;
  if (f.risk.length) query.risk = f.risk;
  if (f.principal) query.principal = f.principal;
  if (f.service) query.service = f.service;
  if (f.since) query.since = localMidnight(f.since);
  // The API's `until` is exclusive, the date picker's is inclusive: send the next midnight.
  if (f.until) query.until = localMidnight(f.until, 1);
  if (f.q) query.q = f.q;
  return query;
}

export const hasNonStatusFilters = (f: Filters): boolean =>
  Boolean(f.risk.length || f.principal || f.service || f.since || f.until || f.q);

/** A status chip: which statuses it selects and how many changes that is. */
export interface StatusChip {
  key: string;
  label: string;
  statuses: ChangeStatus[];
  /** Hidden at zero unless selected; the mockup's four chips always show. */
  optional?: boolean;
}

export const STATUS_CHIPS: StatusChip[] = [
  { key: "all", label: "All", statuses: [] },
  { key: "detected", label: "Detected", statuses: ["detected"], optional: true },
  { key: "awaiting_review", label: "Awaiting review", statuses: ["awaiting_review"] },
  { key: "reverting", label: "Reverting", statuses: ["reverting"] },
  { key: "codifying", label: "Codifying", statuses: ["codifying"], optional: true },
  { key: "failed", label: "Failed", statuses: ["failed"], optional: true },
  // openapi.yaml → listChanges: "The Closed chip sends reverted, accepted, and codified."
  { key: "closed", label: "Closed", statuses: ["reverted", "accepted", "codified"] },
];

export function chipCount(chip: StatusChip, counts: ChangeCounts): number {
  if (chip.statuses.length === 0) return counts.total;
  return chip.statuses.reduce((sum, s) => sum + (counts.by_status[s] ?? 0), 0);
}

export function chipSelected(chip: StatusChip, selected: ChangeStatus[]): boolean {
  return chip.statuses.length === selected.length && chip.statuses.every((s) => selected.includes(s));
}

function unique<T>(values: T[]): T[] {
  return [...new Set(values)];
}
