import { useCallback, useMemo, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { listChanges, type ChangeCounts, type ChangePage, type ChangeSummary, type Risk } from "../../api";
import {
  changeLabel,
  fullTime,
  incidentTitle,
  principalLabel,
  relativeTime,
  RISK_LABELS,
  STATUS_LABELS,
  VIA_LABELS,
} from "../../lib/format";
import { Pagination } from "../../components/Pagination";
import { useNow } from "../../lib/useNow";
import { useResource } from "../../lib/useResource";
import { DebouncedInput } from "./DebouncedInput";
import {
  chipCount,
  chipSelected,
  hasNonStatusFilters,
  parseFilters,
  SERVICE_PATTERN,
  STATUS_CHIPS,
  toQuery,
  type Filters,
} from "./filters";

const SAMPLE_DATA = import.meta.env.MODE === "mock";

export function ChangeLogPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  const query = useMemo(() => toQuery(filters), [filters]);
  const key = JSON.stringify(query);
  const { data, error, loading, reload } = useResource(key, (signal) => listChanges(query, signal));
  const now = useNow();

  /** Applies a filter change to the URL and goes back to page 1. */
  const update = useCallback(
    (change: (next: URLSearchParams) => void, { replace = false } = {}) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          change(next);
          next.delete("page");
          return next;
        },
        { replace },
      );
    },
    [setParams],
  );
  const setText = useCallback(
    (name: string) => (value: string) =>
      update((next) => (value.trim() ? next.set(name, value.trim()) : next.delete(name)), { replace: true }),
    [update],
  );

  const goToPage = (page: number) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (page <= 1) next.delete("page");
      else next.set("page", String(page));
      return next;
    });

  const clearFilters = () => setParams(new URLSearchParams());

  return (
    <div className="change-log">
      <div className="page-head">
        <div>
          <h1>Change log</h1>
          <p className="lede">Every write to a Terraform-managed resource made outside the pipeline.</p>
        </div>
        <div className="search field">
          <label htmlFor="change-search">Search</label>
          <DebouncedInput
            id="change-search"
            type="search"
            placeholder="Resource, principal, or event name"
            maxLength={256}
            value={filters.q}
            onCommit={setText("q")}
          />
        </div>
      </div>

      <StatTiles counts={data?.counts} />

      <div className="status-row" role="group" aria-label="Status">
        <span className="status-row-label" aria-hidden="true">
          Status
        </span>
        {STATUS_CHIPS.map((chip) => {
          const selected = chipSelected(chip, filters.status);
          const count = data ? chipCount(chip, data.counts) : undefined;
          if (chip.optional && !selected && !count) return null;
          return (
            <button
              key={chip.key}
              type="button"
              className="chip"
              aria-pressed={selected}
              onClick={() =>
                update((next) => {
                  next.delete("status");
                  chip.statuses.forEach((s) => next.append("status", s));
                })
              }
            >
              {chip.label}
              {count !== undefined && ` (${count})`}
            </button>
          );
        })}
      </div>

      <FilterBar filters={filters} update={update} setText={setText} onClear={clearFilters} />

      <ChangeTable
        page={data}
        loading={loading}
        error={error?.message}
        onRetry={reload}
        filtered={hasNonStatusFilters(filters) || filters.status.length > 0}
        onClear={clearFilters}
        now={now}
      />

      {data && data.total > 0 && (
        <Pagination page={data} onPage={goToPage} noun={["change", "changes"]} sample={SAMPLE_DATA} />
      )}
    </div>
  );
}

function StatTiles({ counts }: { counts: ChangeCounts | undefined }) {
  const tiles: [string, keyof ChangeCounts][] = [
    ["Awaiting review", "awaiting_review"],
    ["High risk, still open", "high_risk_open"],
    ["Linked to an incident", "linked_to_incident"],
    ["Closed this week", "closed_last_7_days"],
  ];
  return (
    <ul className="tiles" aria-label="Summary">
      {tiles.map(([label, field]) => (
        <li key={field} className="tile">
          <span className="tile-label">{label}</span>
          <span className="tile-value">{counts ? String(counts[field]) : "–"}</span>
        </li>
      ))}
    </ul>
  );
}

function FilterBar({
  filters,
  update,
  setText,
  onClear,
}: {
  filters: Filters;
  update: (change: (next: URLSearchParams) => void) => void;
  setText: (name: string) => (value: string) => void;
  onClear: () => void;
}) {
  const datesBackwards = Boolean(filters.since && filters.until && filters.since > filters.until);
  const setOrDelete = (name: string, value: string) =>
    update((next) => (value ? next.set(name, value) : next.delete(name)));

  return (
    <div className="filters" role="group" aria-label="Filters">
      <div className="field">
        <label htmlFor="filter-risk">Risk</label>
        <select id="filter-risk" value={filters.risk[0] ?? ""} onChange={(e) => setOrDelete("risk", e.target.value)}>
          <option value="">Any</option>
          {(["high", "medium", "low"] as Risk[]).map((r) => (
            <option key={r} value={r}>
              {RISK_LABELS[r]}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="filter-principal">Principal</label>
        <DebouncedInput
          id="filter-principal"
          placeholder="jdev or role name"
          maxLength={256}
          value={filters.principal}
          onCommit={setText("principal")}
        />
      </div>
      <div className="field">
        <label htmlFor="filter-service">Service</label>
        <DebouncedInput
          id="filter-service"
          placeholder="iam, ec2, s3"
          maxLength={64}
          value={filters.service}
          normalize={(v) => v.trim().toLowerCase()}
          validate={(v) => (v === "" || SERVICE_PATTERN.test(v) ? null : "Lowercase letters, digits, and dashes only")}
          onCommit={setText("service")}
        />
      </div>
      <div className="field">
        <label htmlFor="filter-since">From</label>
        <input
          id="filter-since"
          type="date"
          value={filters.since}
          max={filters.until || undefined}
          onChange={(e) => setOrDelete("since", e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="filter-until">To</label>
        <input
          id="filter-until"
          type="date"
          value={filters.until}
          min={filters.since || undefined}
          onChange={(e) => setOrDelete("until", e.target.value)}
          aria-invalid={datesBackwards || undefined}
          aria-describedby={datesBackwards ? "filter-dates-error" : undefined}
        />
      </div>
      {(hasNonStatusFilters(filters) || filters.status.length > 0) && (
        <button type="button" className="link-button clear-filters" onClick={onClear}>
          Clear filters
        </button>
      )}
      {datesBackwards && (
        <p id="filter-dates-error" className="form-error filters-error" role="alert">
          "From" is after "To", so nothing can match.
        </p>
      )}
    </div>
  );
}

function ChangeTable({
  page,
  loading,
  error,
  onRetry,
  filtered,
  onClear,
  now,
}: {
  page: ChangePage | undefined;
  loading: boolean;
  error: string | undefined;
  onRetry: () => void;
  filtered: boolean;
  onClear: () => void;
  now: number;
}) {
  let body: ReactNode;
  if (error && !page) {
    body = (
      <EmptyRow>
        <p role="alert">Couldn't load changes: {error}</p>
        <button type="button" className="button button-secondary" onClick={onRetry}>
          Retry
        </button>
      </EmptyRow>
    );
  } else if (!page) {
    body = (
      <EmptyRow>
        <p role="status">Loading changes…</p>
      </EmptyRow>
    );
  } else if (page.items.length === 0) {
    body = (
      <EmptyRow>
        {filtered ? (
          <>
            <p>No changes match these filters.</p>
            <button type="button" className="link-button" onClick={onClear}>
              Clear filters
            </button>
          </>
        ) : page.total > 0 ? (
          <p>This page is past the last change.</p>
        ) : (
          <p>
            No out-of-band changes yet. The intake indicator in the header shows whether CloudTrail events are
            arriving, so this means nothing changed.
          </p>
        )}
      </EmptyRow>
    );
  } else {
    body = page.items.map((c) => <ChangeRow key={c.id} change={c} now={now} />);
  }

  return (
    <div className="table-wrap" aria-busy={loading}>
      {error && page && (
        <p className="table-banner" role="alert">
          Couldn't refresh: {error}{" "}
          <button type="button" className="link-button" onClick={onRetry}>
            Retry
          </button>
        </p>
      )}
      <table className="changes">
        <caption className="visually-hidden">Changes, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Change</th>
            <th scope="col">Resource</th>
            <th scope="col">Who</th>
            <th scope="col">Via</th>
            <th scope="col">Risk</th>
            <th scope="col">Status</th>
            <th scope="col">When</th>
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </div>
  );
}

function EmptyRow({ children }: { children: ReactNode }) {
  return (
    <tr>
      <td colSpan={7} className="empty">
        {children}
      </td>
    </tr>
  );
}

function ChangeRow({ change: c, now }: { change: ChangeSummary; now: number }) {
  const who = principalLabel(c.principal_arn, c.session_name);
  return (
    <tr>
      <td className="col-change">
        <Link to={`/changes/${c.id}`} className="change-title">
          {c.title}
        </Link>
        <span className="sub mono">
          <span className="change-id">{changeLabel(c.id)}</span> · {c.event_name}
        </span>
      </td>
      <td>
        <span className="cell-main" title={c.resource_arn}>
          {c.resource_name ?? c.resource_arn.split(/[:/]/).at(-1)}
        </span>
        <span className="sub mono">{c.tf_address ?? <span className="muted">no Terraform address</span>}</span>
      </td>
      <td>
        <span className="cell-main">{who.who}</span>
        <span className="sub mono" title={c.principal_arn}>
          {who.via}
        </span>
      </td>
      <td>
        <span className="via mono">{VIA_LABELS[c.via]}</span>
      </td>
      <td>
        {c.risk ? (
          <span className={`pill risk-${c.risk}`}>{RISK_LABELS[c.risk]}</span>
        ) : (
          <span className="muted">Pending</span>
        )}
      </td>
      <td>
        <span className={`pill status-${c.status}`}>{STATUS_LABELS[c.status]}</span>
        {c.linked_incidents.map((inc) => (
          <Link key={inc.id} to={`/incidents/${inc.id}`} className="linked-incident">
            Linked to {incidentTitle(inc)}
          </Link>
        ))}
      </td>
      <td className="col-when">
        <time dateTime={c.event_time} title={fullTime(c.event_time)}>
          {relativeTime(c.event_time, now)}
        </time>
      </td>
    </tr>
  );
}
