import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  acceptChange,
  ApiError,
  getChange,
  getIntakeHealth,
  listEvidence,
  revertChange,
  type Change,
  type ChangeStatus,
  type Drift,
} from "../../api";
import { useUser } from "../../auth/AuthProvider";
import { AdminButton } from "../../components/AdminButton";
import { AnalysisDetails } from "../../components/AnalysisDetails";
import { EvidenceList } from "../../components/EvidenceList";
import { OutcomeNote } from "../../components/OutcomeNote";
import {
  changeLabel,
  fullTime,
  INCIDENT_STATUS_LABELS,
  incidentTitle,
  relativeTime,
  resourceId,
  RISK_LABELS,
  safeHttpUrl,
  STATUS_LABELS,
  utcTime,
  VIA_DETAIL,
  VIA_PHRASE,
} from "../../lib/format";
import { outcomeFor, type Outcome } from "../../lib/outcome";
import { useNow } from "../../lib/useNow";
import { useResource } from "../../lib/useResource";

/** While the agent hasn't written its summary, or a revert or codify run is going, the page polls. */
export const CHANGE_POLL_MS = 5_000;
const POLLING: ChangeStatus[] = ["detected", "reverting", "codifying"];
// app/services/change_state.py: Revert from review or after a failed run; Accept also before the summary.
const REVERTABLE: ChangeStatus[] = ["awaiting_review", "failed"];
const ACCEPTABLE: ChangeStatus[] = ["detected", "awaiting_review", "failed"];

export function ChangeDetailPage() {
  const { id: rawId } = useParams();
  const id = Number(rawId);
  if (!Number.isInteger(id) || id <= 0) return <NotFound label={rawId ?? ""} />;
  // Keyed so a different change starts from a clean state (note, dialog, notices).
  return <ChangeDetail key={id} id={id} />;
}

function NotFound({ label }: { label: string }) {
  return (
    <section className="placeholder">
      <h1>Change not found</h1>
      <p className="muted">There's no change {label}.</p>
      <Link to="/changes">← Change log</Link>
    </section>
  );
}

function ChangeDetail({ id }: { id: number }) {
  const [polling, setPolling] = useState(true);
  const change = useResource(`change-${id}`, (signal) => getChange(id, signal), {
    pollMs: polling ? CHANGE_POLL_MS : undefined,
  });
  const data = change.data;
  const shouldPoll = !data || POLLING.includes(data.status);
  if (data && shouldPoll !== polling) setPolling(shouldPoll);
  const evidence = useResource(`change-evidence-${id}-${data?.analysis?.id ?? "none"}`, (signal) =>
    listEvidence("changes", id, signal),
  );
  // Revert needs AWS: in replay mode there is nothing to re-apply (openapi.yaml → revertChange).
  const intake = useResource("intake-for-revert", getIntakeHealth);
  const replay = intake.data?.state === "replay";
  const now = useNow();

  if (change.error?.status === 404) return <NotFound label={changeLabel(id)} />;
  if (!data) {
    return change.error ? (
      <section className="placeholder">
        <h1>{changeLabel(id)}</h1>
        <p role="alert">Couldn't load this change: {change.error.message}</p>
        <button type="button" className="button button-secondary" onClick={change.reload}>
          Retry
        </button>
      </section>
    ) : (
      <p className="muted" role="status">
        Loading {changeLabel(id)}…
      </p>
    );
  }

  const where = data.tf_address
    ? data.via === "terraform_outside_pipeline"
      ? "not through the pipeline"
      : "not in Terraform"
    : "resource not in Terraform state";

  return (
    <article className="detail">
      <Link to="/changes" className="back-link">
        ← Change log
      </Link>
      <div className="detail-tags">
        {data.risk ? (
          <span className={`pill risk-${data.risk}`}>{RISK_LABELS[data.risk]} risk</span>
        ) : (
          <span className="pill risk-unknown">Risk not assessed</span>
        )}
        <span className={`pill status-${data.status}`}>{STATUS_LABELS[data.status]}</span>
        <span className="mono change-id">{changeLabel(data.id)}</span>
        {POLLING.includes(data.status) && (
          <span className="muted small live-note" role="status">
            Refreshing every {CHANGE_POLL_MS / 1000} s
          </span>
        )}
      </div>
      <h1 className="detail-title">{data.title}</h1>
      <p className="lede">
        Detected{" "}
        <time dateTime={data.detected_at} title={fullTime(data.detected_at)}>
          {relativeTime(data.detected_at, now)}
        </time>{" "}
        · {VIA_PHRASE[data.via]} · {where}
      </p>
      {change.error && (
        <p className="table-banner" role="alert">
          Couldn't refresh: {change.error.message}{" "}
          <button type="button" className="link-button" onClick={change.reload}>
            Retry
          </button>
        </p>
      )}

      <div className="detail-grid">
        <div className="detail-main">
          <section className="card" aria-labelledby="summary-heading">
            <h2 id="summary-heading">Agent summary</h2>
            {data.analysis ? (
              <div className="analysis">
                <AnalysisDetails analysis={data.analysis} />
              </div>
            ) : (
              <p className="muted" role={data.status === "detected" ? "status" : undefined}>
                {data.status === "detected"
                  ? "The agent is looking into this change. Its summary and risk appear here when the run finishes."
                  : "No analysis was recorded for this change."}
              </p>
            )}
          </section>
          <section className="card" aria-labelledby="drift-heading">
            <h2 id="drift-heading">Terraform expects vs. live</h2>
            <DriftView drift={data.drift} change={data} />
          </section>
          <section className="card" aria-labelledby="evidence-heading">
            <h2 id="evidence-heading">Evidence</h2>
            <p className="muted small card-lede">
              The agent's read-only tool calls, oldest first. Results are redacted and shown as plain text.
            </p>
            <EvidenceList evidence={evidence} />
          </section>
        </div>

        <aside className="detail-side">
          <DecisionCard change={data} replay={replay} onChanged={change.reload} />
          <WhatHappened change={data} />
          <section className="card" aria-labelledby="linked-heading">
            <h2 id="linked-heading">Linked incidents</h2>
            {data.linked_incidents.length === 0 ? (
              <p className="muted">
                None yet. ConsoleTrack links one here automatically if an alert traces back to this change.
              </p>
            ) : (
              <ul className="linked-list">
                {data.linked_incidents.map(({ incident, confidence }) => (
                  <li key={incident.id}>
                    <Link to={`/incidents/${incident.id}`}>{incidentTitle(incident)}</Link>
                    <span className="sub">
                      {INCIDENT_STATUS_LABELS[incident.status]} · {confidence} confidence
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </article>
  );
}

// --- Drift ---------------------------------------------------------------------------------------

const DRIFT_MARK = { context: " ", added: "+", removed: "−" } as const;
const DRIFT_SPOKEN = { context: "In both", added: "Live only", removed: "Terraform only" } as const;

function DriftView({ drift, change }: { drift: Drift | null; change: Change }) {
  if (!drift) {
    return (
      <p className="muted">
        {change.tf_address
          ? "No drift recorded yet. It's built from the CloudTrail request and the recorded Terraform state once the agent has looked the resource up."
          : "This resource isn't in the demo-app root's Terraform state, so there's nothing to compare."}
      </p>
    );
  }
  return (
    <>
      <p className="muted small card-lede">
        <span className="mono">{drift.tf_address}</span> · {drift.attribute}
      </p>
      <ol className="drift" aria-label={`${drift.attribute}: Terraform vs. live`}>
        {drift.lines.map((line, index) => (
          <li key={`${index}-${line.text}`} className={`drift-${line.op}`}>
            <span className="drift-mark" aria-hidden="true">
              {DRIFT_MARK[line.op]}
            </span>
            <span className="visually-hidden">{DRIFT_SPOKEN[line.op]}: </span>
            <span className="drift-text">
              {line.text}
              {line.note && <span className="drift-note"> # {line.note}</span>}
            </span>
          </li>
        ))}
      </ol>
    </>
  );
}

// --- Decision: Revert, Accept, Codify ----------------------------------------------------------

function DecisionCard({ change, replay, onChanged }: { change: Change; replay: boolean; onChanged: () => void }) {
  const reviewable = ACCEPTABLE.includes(change.status);
  // Kept here, not in ReviewActions: a decision moves the change out of review, which unmounts
  // the actions, and the result ("Revert started", with the run link) must stay on screen.
  const [outcome, setOutcome] = useState<Outcome>(null);
  return (
    <section className="card" aria-labelledby="decision-heading">
      <h2 id="decision-heading">Decision</h2>
      <div className="decision">
        <DecisionStatus change={change} />
        {reviewable && <ReviewActions change={change} replay={replay} onOutcome={setOutcome} onChanged={onChanged} />}
        <OutcomeNote outcome={outcome} />
      </div>
    </section>
  );
}

/** What has been decided so far, and the revert run if there is one. */
function DecisionStatus({ change }: { change: Change }) {
  const r = change.remediation;
  const run = safeHttpUrl(r?.external_url);
  const by = change.decided_by && change.decided_at && (
    <>
      {" "}
      by {change.decided_by}{" "}
      <time dateTime={change.decided_at} title={fullTime(change.decided_at)}>
        {relativeTime(change.decided_at)}
      </time>
    </>
  );
  const runLink = run && (
    <a href={run} target="_blank" rel="noopener noreferrer">
      {r?.kind === "codify_pr" ? "Pull request" : "Workflow run"}
    </a>
  );

  switch (change.status) {
    case "reverting":
      return (
        <div className="decision-status pending" role="status">
          <p>
            <strong>Reverting to Terraform.</strong> Requested{by}.
          </p>
          <p className="muted small">
            The workflow waits in the <span className="mono">consoletrack-revert</span> environment until a reviewer
            approves the plan in GitHub, then re-applies the Terraform root. This page refreshes until it finishes.
          </p>
          {runLink ? <p>{runLink}</p> : <p className="muted small">The run link appears when GitHub returns it.</p>}
          {change.reason && <blockquote className="decision-note">{change.reason}</blockquote>}
        </div>
      );
    case "reverted":
      return (
        <div className="decision-status ok">
          <p>
            <strong>Reverted to Terraform</strong>
            {by}.
          </p>
          {runLink && <p>{runLink}</p>}
          {change.reason && <blockquote className="decision-note">{change.reason}</blockquote>}
        </div>
      );
    case "accepted":
      return (
        <div className="decision-status ok">
          <p>
            <strong>Accepted</strong>
            {by}.
          </p>
          {change.reason && <blockquote className="decision-note">{change.reason}</blockquote>}
        </div>
      );
    case "codifying":
    case "codified":
      return (
        <div className="decision-status ok">
          <p>
            <strong>{change.status === "codified" ? "Codified in Terraform" : "Codifying"}</strong>
            {by}.
          </p>
          {runLink && <p>{runLink}</p>}
        </div>
      );
    case "failed":
      return (
        <div className="decision-status failed" role="alert">
          <p>
            <strong>The {change.decision === "codify" ? "codify" : "revert"} run failed.</strong>{" "}
            {r?.error ?? "See the run for details."}
          </p>
          {runLink && <p>{runLink}</p>}
          <p className="small">Review the change again: revert, or accept it with a note.</p>
        </div>
      );
    default:
      return null;
  }
}

function ReviewActions({
  change,
  replay,
  onOutcome: setOutcome,
  onChanged,
}: {
  change: Change;
  replay: boolean;
  onOutcome: (outcome: Outcome) => void;
  onChanged: () => void;
}) {
  const { role } = useUser();
  const noteId = useId();
  const revertHintId = useId();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"accept" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const revertBlocked = replay
    ? "AWS required"
    : !REVERTABLE.includes(change.status)
      ? "Available once the agent has summarized the change"
      : null;

  const onAccept = async (e: FormEvent) => {
    e.preventDefault();
    const text = note.trim();
    if (!text || role !== "admin") return;
    setBusy("accept");
    setOutcome(null);
    try {
      await acceptChange(change.id, text);
      setOutcome({ kind: "done", text: "Change accepted. The note is its record." });
      onChanged();
    } catch (err) {
      setOutcome(outcomeFor(err));
      if (err instanceof ApiError && err.status === 409) onChanged();
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <AdminButton
        className="button button-accent full"
        disabledReason={revertBlocked}
        aria-describedby={revertHintId}
        onClick={() => {
          setOutcome(null);
          setConfirming(true);
        }}
      >
        Revert to Terraform
      </AdminButton>
      <p id={revertHintId} className="muted small">
        {replay
          ? "Replay mode has no AWS account to revert in. Run against AWS to revert."
          : "Starts the gated revert workflow. A reviewer approves the plan in GitHub before anything changes."}
      </p>
      {confirming && (
        <RevertDialog
          change={change}
          onClose={() => setConfirming(false)}
          onDone={(result) => {
            setConfirming(false);
            setOutcome(result);
            onChanged();
          }}
        />
      )}

      <form className="accept-form" onSubmit={(e) => void onAccept(e)}>
        <label htmlFor={noteId}>Note (required to accept)</label>
        <textarea
          id={noteId}
          rows={3}
          maxLength={2000}
          placeholder="Why this change is intended"
          value={note}
          disabled={role !== "admin"}
          onChange={(e) => setNote(e.target.value)}
        />
        <AdminButton
          type="submit"
          className="button button-secondary full"
          disabled={busy !== null || !note.trim()}
        >
          {busy === "accept" ? "Accepting…" : "Accept with note"}
        </AdminButton>
      </form>
      <AdminButton className="button button-phase full" disabledReason="Codify ships in Phase 2">
        Codify as Terraform PR · Phase 2
      </AdminButton>
    </>
  );
}

/** Confirm, with an optional note, then POST /changes/{id}/revert. A modal <dialog>: focus stays inside. */
function RevertDialog({
  change,
  onClose,
  onDone,
}: {
  change: Change;
  onClose: () => void;
  onDone: (outcome: Outcome) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const noteId = useId();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Outcome>(null);
  const root = change.drift?.tf_root ?? "terraform/demo-app/us-east-1";
  // Whatever had focus when the dialog opened (the Revert button) gets it back on close. Read once
  // at first render: StrictMode runs the effect twice, and by then focus is inside the dialog.
  const [opener] = useState(() => document.activeElement as HTMLElement | null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    // jsdom (Vitest) has no showModal; there the dialog just opens in place.
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    return () => opener?.focus();
  }, [opener]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { run_url, change: updated } = await revertChange(change.id, note.trim() || null);
      const run = safeHttpUrl(run_url ?? updated.remediation?.external_url);
      onDone({
        kind: "done",
        text: (
          <>
            Revert started. A reviewer approves the plan in GitHub before anything changes.{" "}
            {run ? (
              <a href={run} target="_blank" rel="noopener noreferrer">
                Open the workflow run
              </a>
            ) : (
              "The run link appears when GitHub returns it."
            )}
          </>
        ),
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 501) {
        // The backend ships Revert with #14; until then it answers 501 and nothing is dispatched.
        onDone({
          kind: "unavailable",
          text: (
            <>
              <strong>Revert isn't available yet.</strong> The backend answered 501 Not Implemented ({err.message}).
              Nothing was dispatched and the change is still awaiting review.
            </>
          ),
        });
        return;
      }
      if (err instanceof ApiError && err.status === 409) {
        onDone({ kind: "error", text: `Couldn't revert: ${err.message}` });
        return;
      }
      setError(outcomeFor(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form method="dialog" onSubmit={(e) => void onSubmit(e)}>
        <h2 id={titleId}>Revert {changeLabel(change.id)} to Terraform?</h2>
        <p>
          This dispatches the revert workflow for <span className="mono">{root}</span>
          {change.tf_address && (
            <>
              {" "}
              (<span className="mono">{change.tf_address}</span>)
            </>
          )}
          . The run waits in the <span className="mono">consoletrack-revert</span> environment until a reviewer
          approves the plan in GitHub; nothing changes in AWS before that.
        </p>
        <label htmlFor={noteId}>Note (optional)</label>
        <textarea
          id={noteId}
          rows={3}
          maxLength={2000}
          placeholder="Why it's being reverted"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <OutcomeNote outcome={error} />
        <div className="form-actions">
          <button type="submit" className="button button-accent" disabled={busy}>
            {busy ? "Starting…" : "Start revert"}
          </button>
          <button type="button" className="button button-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </dialog>
  );
}

// --- What happened -------------------------------------------------------------------------------

function WhatHappened({ change: c }: { change: Change }) {
  const principal = c.principal_arn.split(":").slice(5).join(":") || c.principal_arn;
  const params = c.request_parameters ? JSON.stringify(c.request_parameters, null, 2) : null;
  return (
    <section className="card" aria-labelledby="what-heading">
      <h2 id="what-heading">What happened</h2>
      <dl className="facts">
        <dt>Principal</dt>
        <dd className="mono" title={c.principal_arn}>
          {principal}
        </dd>
        <dt>Via</dt>
        <dd>
          {VIA_DETAIL[c.via]}
          {c.user_agent && <span className="sub mono">{c.user_agent}</span>}
        </dd>
        <dt>Event</dt>
        <dd className="mono">{c.event_name}</dd>
        <dt>Event time</dt>
        <dd>
          <time dateTime={c.event_time}>{utcTime(c.event_time)}</time>
        </dd>
        <dt>Region</dt>
        <dd className="mono">{c.region}</dd>
        <dt>Source IP</dt>
        <dd className="mono">{c.source_ip ?? <span className="muted">Unknown</span>}</dd>
        <dt>Resource</dt>
        <dd className="mono" title={c.resource_arn}>
          {resourceId(c.resource_arn)}
          {c.resource_name && ` (${c.resource_name})`}
        </dd>
        <dt>Terraform address</dt>
        <dd className="mono">
          {c.tf_address ? (
            [c.drift?.tf_root, c.tf_address].filter(Boolean).join(" · ")
          ) : (
            <span className="muted">Not in Terraform state</span>
          )}
        </dd>
        <dt>CloudTrail event ID</dt>
        <dd className="mono">{c.cloudtrail_event_id}</dd>
        {params && (
          <>
            <dt>Request</dt>
            <dd>
              <details className="request-params">
                <summary>Request parameters</summary>
                <pre aria-label="Request parameters">{params}</pre>
              </details>
            </dd>
          </>
        )}
      </dl>
    </section>
  );
}
