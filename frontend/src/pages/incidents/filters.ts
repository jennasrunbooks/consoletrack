// Incident list filters live in the URL, like the change log's (pages/changes/filters.ts): a
// filtered view can be shared and survives reloads. This maps the URL to GET /incidents and back.
import type { IncidentStatus, ListIncidentsQuery } from "../../api";

export const PAGE_SIZE = 25;

const STATUSES: IncidentStatus[] = ["open", "triaging", "awaiting_review", "remediating", "resolved", "closed"];
/** The severities the demo's alert rules use; the API takes any label value. */
export const SEVERITIES = ["critical", "warning", "info"] as const;
const SEVERITY_PATTERN = /^[a-z0-9_-]{1,64}$/;

export interface IncidentFilters {
  status: IncidentStatus[];
  severity: string;
  page: number;
}

/** Parses the URL leniently: unknown values are dropped rather than sent to the API. */
export function parseIncidentFilters(params: URLSearchParams): IncidentFilters {
  const page = Number.parseInt(params.get("page") ?? "1", 10);
  const severity = (params.get("severity") ?? "").trim().toLowerCase();
  return {
    status: [
      ...new Set(params.getAll("status").filter((s): s is IncidentStatus => STATUSES.includes(s as IncidentStatus))),
    ],
    severity: SEVERITY_PATTERN.test(severity) ? severity : "",
    page: Number.isInteger(page) && page > 0 ? page : 1,
  };
}

export function toIncidentQuery(f: IncidentFilters): ListIncidentsQuery {
  const query: ListIncidentsQuery = { page: f.page, page_size: PAGE_SIZE };
  if (f.status.length) query.status = f.status;
  if (f.severity) query.severity = f.severity;
  return query;
}

export interface IncidentChip {
  key: string;
  label: string;
  statuses: IncidentStatus[];
}

/** No counts: GET /incidents doesn't return any, unlike GET /changes. */
export const INCIDENT_CHIPS: IncidentChip[] = [
  { key: "all", label: "All", statuses: [] },
  // Everything a person may still need to act on or watch.
  { key: "active", label: "Active", statuses: ["open", "triaging", "awaiting_review", "remediating"] },
  { key: "awaiting_review", label: "Awaiting review", statuses: ["awaiting_review"] },
  { key: "resolved", label: "Resolved", statuses: ["resolved"] },
  { key: "closed", label: "Closed", statuses: ["closed"] },
];

export function chipSelected(chip: IncidentChip, selected: IncidentStatus[]): boolean {
  return chip.statuses.length === selected.length && chip.statuses.every((s) => selected.includes(s));
}
