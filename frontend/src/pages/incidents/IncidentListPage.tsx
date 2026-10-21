import { useMemo, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { listIncidents, type IncidentSummary } from "../../api";
import { Pagination } from "../../components/Pagination";
import {
  changeLabel,
  duration,
  fullTime,
  INCIDENT_STATUS_LABELS,
  incidentLabel,
  relativeTime,
} from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { useResource } from "../../lib/useResource";
import { chipSelected, INCIDENT_CHIPS, parseIncidentFilters, SEVERITIES, toIncidentQuery } from "./filters";

const SAMPLE_DATA = import.meta.env.MODE === "mock";
const COLUMNS = 6;

export function IncidentListPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseIncidentFilters(params), [params]);
  const query = useMemo(() => toIncidentQuery(filters), [filters]);
  const { data, error, loading, reload } = useResource(JSON.stringify(query), (signal) =>
    listIncidents(query, signal),
  );
  const now = useNow();
  const filtered = filters.status.length > 0 || Boolean(filters.severity);
  // A severity from a shared URL that isn't one of ours still shows as selected.
  const severityOptions: string[] = [...SEVERITIES];
  if (filters.severity && !severityOptions.includes(filters.severity)) severityOptions.push(filters.severity);

  /** Applies a filter change to the URL and goes back to page 1. */
  const update = (change: (next: URLSearchParams) => void) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      change(next);
      next.delete("page");
      return next;
    });

  const goToPage = (page: number) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (page <= 1) next.delete("page");
      else next.set("page", String(page));
      return next;
    });

  const clearFilters = () => setParams(new URLSearchParams());

  let body: ReactNode;
  if (error && !data) {
    body = (
      <EmptyRow>
        <p role="alert">Couldn't load incidents: {error.message}</p>
        <button type="button" className="button button-secondary" onClick={reload}>
          Retry
        </button>
      </EmptyRow>
    );
  } else if (!data) {
    body = (
      <EmptyRow>
        <p role="status">Loading incidents…</p>
      </EmptyRow>
    );
  } else if (data.items.length === 0) {
    body = (
      <EmptyRow>
        {filtered ? (
          <>
            <p>No incidents match these filters.</p>
            <button type="button" className="link-button" onClick={clearFilters}>
              Clear filters
            </button>
          </>
        ) : data.total > 0 ? (
          <p>This page is past the last incident.</p>
        ) : (
          <p>No incidents yet. One opens when Alertmanager sends ConsoleTrack a firing alert.</p>
        )}
      </EmptyRow>
    );
  } else {
    body = data.items.map((incident) => <IncidentRow key={incident.id} incident={incident} now={now} />);
  }

  return (
    <div className="incident-list">
      <div className="page-head">
        <div>
          <h1>Incidents</h1>
          <p className="lede">Alerts from the cluster, the agent's triage, and the fix it proposes.</p>
        </div>
      </div>

      <div className="status-row" role="group" aria-label="Status">
        <span className="status-row-label" aria-hidden="true">
          Status
        </span>
        {INCIDENT_CHIPS.map((chip) => (
          <button
            key={chip.key}
            type="button"
            className="chip"
            aria-pressed={chipSelected(chip, filters.status)}
            onClick={() =>
              update((next) => {
                next.delete("status");
                chip.statuses.forEach((s) => next.append("status", s));
              })
            }
          >
            {chip.label}
          </button>
        ))}
      </div>

      <div className="filters" role="group" aria-label="Filters">
        <div className="field">
          <label htmlFor="filter-severity">Severity</label>
          <select
            id="filter-severity"
            value={filters.severity}
            onChange={(e) => {
              const value = e.target.value;
              update((next) => (value ? next.set("severity", value) : next.delete("severity")));
            }}
          >
            <option value="">Any</option>
            {severityOptions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        {filtered && (
          <button type="button" className="link-button clear-filters" onClick={clearFilters}>
            Clear filters
          </button>
        )}
      </div>

      <div className="table-wrap" aria-busy={loading}>
        {error && data && (
          <p className="table-banner" role="alert">
            Couldn't refresh: {error.message}{" "}
            <button type="button" className="link-button" onClick={reload}>
              Retry
            </button>
          </p>
        )}
        <table className="changes incidents">
          <caption className="visually-hidden">Incidents, newest first</caption>
          <thead>
            <tr>
              <th scope="col">Incident</th>
              <th scope="col">Workload</th>
              <th scope="col">Severity</th>
              <th scope="col">Status</th>
              <th scope="col">Opened</th>
              <th scope="col">Time to resolve</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>

      {data && data.total > 0 && (
        <Pagination page={data} onPage={goToPage} noun={["incident", "incidents"]} sample={SAMPLE_DATA} />
      )}
    </div>
  );
}

function EmptyRow({ children }: { children: ReactNode }) {
  return (
    <tr>
      <td colSpan={COLUMNS} className="empty">
        {children}
      </td>
    </tr>
  );
}

export function SeverityPill({ severity }: { severity: string | null }) {
  if (!severity) return <span className="muted">None</span>;
  return <span className={`pill severity-${severity === "critical" || severity === "warning" ? severity : "other"}`}>{severity}</span>;
}

function IncidentRow({ incident: i, now }: { incident: IncidentSummary; now: number }) {
  const resolvedIn = i.resolved_at ? Math.max(0, Math.round((Date.parse(i.resolved_at) - Date.parse(i.opened_at)) / 1000)) : null;
  return (
    <tr>
      <td className="col-change">
        <Link to={`/incidents/${i.id}`} className="change-title">
          {i.alertname}
        </Link>
        <span className="sub mono">
          <span className="change-id">{incidentLabel(i.id)}</span>
        </span>
        {i.linked_changes.map((c) => (
          <Link key={c.id} to={`/changes/${c.id}`} className="linked-incident">
            Linked to {changeLabel(c.id)}: {c.title}
          </Link>
        ))}
      </td>
      <td>
        <span className="cell-main">{i.workload ?? <span className="muted">unknown</span>}</span>
        <span className="sub mono">{i.namespace ?? "no namespace"}</span>
      </td>
      <td>
        <SeverityPill severity={i.severity} />
      </td>
      <td>
        <span className={`pill incident-${i.status}`}>{INCIDENT_STATUS_LABELS[i.status]}</span>
      </td>
      <td className="col-when">
        <time dateTime={i.opened_at} title={fullTime(i.opened_at)}>
          {relativeTime(i.opened_at, now)}
        </time>
      </td>
      <td className="col-when">
        {resolvedIn !== null ? (
          <span title={i.resolved_at ? `Resolved ${fullTime(i.resolved_at)}` : undefined}>{duration(resolvedIn)}</span>
        ) : i.status === "closed" ? (
          <span className="muted">Closed unresolved</span>
        ) : (
          <span className="muted">Open {duration(Math.max(0, Math.round((now - Date.parse(i.opened_at)) / 1000)))}</span>
        )}
      </td>
    </tr>
  );
}
