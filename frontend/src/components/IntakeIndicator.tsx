import { useEffect, useId, useRef, useState } from "react";
import { getIntakeHealth, type IntakeHealth, type IntakeState } from "../api";
import { duration, fullTime, INTAKE_LABELS, relativeTime, safeHttpUrl } from "../lib/format";
import { useNow } from "../lib/useNow";
import { useResource } from "../lib/useResource";

export const INTAKE_POLL_MS = 30_000;
// The canary CronJob's schedule (product-spec.md → Intake health and canary).
const CANARY_INTERVAL = "every 15 min";
const TOOLTIP = "Shows whether AWS change events are reaching ConsoleTrack";

const STATES: IntakeState[] = ["live", "delayed", "down", "replay"];

function headline(health: IntakeHealth, now: number): string {
  if (health.state === "replay") return "Replay mode · recorded events";
  const last = health.last_event_at ? `last event ${relativeTime(health.last_event_at, now)}` : "no events yet";
  return `Intake ${INTAKE_LABELS[health.state].toLowerCase()} · ${health.region} · ${last}`;
}

function orUnknown<T>(value: T | null, show: (v: T) => string): string {
  return value === null ? "unknown" : show(value);
}

/**
 * Header button showing whether CloudTrail events are reaching ConsoleTrack, so an empty
 * change log can't be mistaken for a quiet account when intake is broken. Hover or focus
 * explains it; click opens the details from GET /intake/health. Polls every 30 s.
 */
export function IntakeIndicator() {
  const { data: health, error } = useResource("intake", getIntakeHealth, { pollMs: INTAKE_POLL_MS });
  const now = useNow(INTAKE_POLL_MS);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const tipId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  // No answer yet, or it failed with nothing to fall back on: say so instead of guessing "live".
  const state: IntakeState | "unknown" = health && !error ? health.state : "unknown";
  const grafanaUrl = safeHttpUrl(health?.grafana_url);
  const label = health && !error ? headline(health, now) : error ? "Intake status unavailable" : "Checking intake…";

  return (
    <div className="intake" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="intake-button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-describedby={open ? undefined : tipId}
        onClick={() => setOpen((o) => !o)}
        data-state={state}
      >
        <span className={`dot dot-${state}`} aria-hidden="true" />
        <span className="intake-label">{label}</span>
      </button>
      {!open && (
        <span role="tooltip" id={tipId} className="tip tip-below">
          {TOOLTIP}
        </span>
      )}
      {open && (
        <div id={panelId} className="intake-panel" role="region" aria-label="Change intake details">
          <h2 className="intake-title">
            <span className={`dot dot-${state}`} aria-hidden="true" />
            Change intake: {state === "unknown" ? "Unknown" : INTAKE_LABELS[state]}
          </h2>
          <p>
            ConsoleTrack learns about AWS changes from CloudTrail. This shows whether those events are arriving, so an
            empty change log means nothing changed, not that intake is broken.
          </p>
          {error && <p className="intake-error">Couldn't load intake health: {error.message}</p>}
          {health && health.reasons.length > 0 && (
            <ul className="intake-reasons" aria-label="Why">
              {health.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {health && (
            <dl className="intake-details">
              <dt>Watching</dt>
              <dd>{health.region} + IAM (global)</dd>
              <dt>Last event</dt>
              <dd title={health.last_event_at ? fullTime(health.last_event_at) : undefined}>
                {orUnknown(health.last_event_at, (t) => relativeTime(t, now))}
              </dd>
              <dt>Last canary</dt>
              <dd title={health.last_canary_at ? fullTime(health.last_canary_at) : undefined}>
                {orUnknown(health.last_canary_at, (t) => relativeTime(t, now))} · {CANARY_INTERVAL}
              </dd>
              <dt>Queue</dt>
              <dd>
                {orUnknown(health.queue_backlog, (n) => `${n} waiting`)} ·{" "}
                {orUnknown(health.oldest_message_age_seconds, (s) => `oldest ${duration(s)}`)}
              </dd>
              <dt>Dead-letter</dt>
              <dd>{orUnknown(health.dead_letter_count, String)}</dd>
              <dt>Trail</dt>
              <dd>{orUnknown(health.trail_logging, (on) => (on ? "logging" : "not logging"))}</dd>
            </dl>
          )}
          <ul className="intake-key" aria-label="Indicator key">
            {STATES.map((s) => (
              <li key={s}>
                <span className={`dot dot-${s}`} aria-hidden="true" />
                {INTAKE_LABELS[s]}
              </li>
            ))}
          </ul>
          {grafanaUrl ? (
            <a className="intake-link" href={grafanaUrl} target="_blank" rel="noopener noreferrer">
              Open intake dashboard in Grafana →
            </a>
          ) : (
            <p className="muted small">Grafana isn't reachable from this browser.</p>
          )}
        </div>
      )}
    </div>
  );
}
