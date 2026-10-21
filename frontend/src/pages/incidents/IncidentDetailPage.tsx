import { useId, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  approveRemediation,
  getIncident,
  listEvidence,
  rejectRemediation,
  triageIncident,
  type Analysis,
  type Incident,
  type IncidentStatus,
  type Remediation,
  type RemediationKind,
} from "../../api";
import { AdminButton } from "../../components/AdminButton";
import { EvidenceList } from "../../components/EvidenceList";
import { useUser } from "../../auth/AuthProvider";
import {
  changeLabel,
  duration,
  FLAG_LABELS,
  fullTime,
  INCIDENT_STATUS_LABELS,
  incidentLabel,
  proposalReason,
  proposedFix,
  relativeTime,
  REMEDIATION_STATUS_LABELS,
  RISK_LABELS,
  safeHttpUrl,
  STATUS_LABELS,
  WARNING_FLAGS,
} from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { useResource } from "../../lib/useResource";
import { SeverityPill } from "./IncidentListPage";
import { valueChanges } from "./remediation";
import { timelineSteps } from "./timeline";

/** While an incident is open or triaging the page polls, so the analysis appears without a reload. */
export const INCIDENT_POLL_MS = 5_000;
const POLLING: IncidentStatus[] = ["open", "triaging"];
/** Kinds the backend's apply loop can run today (openapi.yaml → approveIncidentRemediation). */
const APPLY_KINDS: RemediationKind[] = ["set_memory_limit"];

/** Open or triaging, or an approved fix still going through the apply loop. */
const isLive = (incident: Incident): boolean =>
  POLLING.includes(incident.status) ||
  incident.remediation?.status === "approved" ||
  incident.remediation?.status === "running";
// openapi.yaml → triageIncident: "Allowed from `open` and `awaiting_review`."
const RERUNNABLE: IncidentStatus[] = ["open", "awaiting_review"];

export function IncidentDetailPage() {
  const { id: rawId } = useParams();
  const id = Number(rawId);
  const valid = Number.isInteger(id) && id > 0;
  if (!valid) return <NotFound label={rawId ?? ""} />;
  // Keyed so a different incident starts from a clean state (forms, notices).
  return <IncidentDetail key={id} id={id} />;
}

function NotFound({ label }: { label: string }) {
  return (
    <section className="placeholder">
      <h1>Incident not found</h1>
      <p className="muted">There's no incident {label}.</p>
      <Link to="/incidents">← Incidents</Link>
    </section>
  );
}

function IncidentDetail({ id }: { id: number }) {
  const [polling, setPolling] = useState(true);
  const incident = useResource(`incident-${id}`, (signal) => getIncident(id, signal), {
    pollMs: polling ? INCIDENT_POLL_MS : undefined,
  });
  const data = incident.data;
  // Poll only while something is happening (triage or the apply loop); stop as soon as it settles.
  const shouldPoll = !data || isLive(data);
  if (data && shouldPoll !== polling) setPolling(shouldPoll);
  const evidence = useResource(`evidence-${id}-${data?.analysis?.id ?? "none"}-${data?.status ?? ""}`, (signal) =>
    listEvidence("incidents", id, signal),
  );
  const now = useNow();

  if (incident.error?.status === 404) return <NotFound label={incidentLabel(id)} />;
  if (!data) {
    return incident.error ? (
      <section className="placeholder">
        <h1>{incidentLabel(id)}</h1>
        <p role="alert">Couldn't load this incident: {incident.error.message}</p>
        <button type="button" className="button button-secondary" onClick={incident.reload}>
          Retry
        </button>
      </section>
    ) : (
      <p className="muted" role="status">
        Loading {incidentLabel(id)}…
      </p>
    );
  }

  return (
    <article className="detail">
      <Link to="/incidents" className="back-link">
        ← Incidents
      </Link>
      <div className="detail-tags">
        <SeverityPill severity={data.severity} />
        <span className={`pill incident-${data.status}`}>{INCIDENT_STATUS_LABELS[data.status]}</span>
        <span className="mono change-id">{incidentLabel(data.id)}</span>
        {isLive(data) && (
          <span className="muted small live-note" role="status">
            Refreshing every {INCIDENT_POLL_MS / 1000} s
          </span>
        )}
      </div>
      <h1 className="detail-title">{data.alertname}</h1>
      <p className="lede">
        {[data.workload, data.namespace && `in ${data.namespace}`].filter(Boolean).join(" ") || "No workload"} · opened{" "}
        <time dateTime={data.opened_at} title={fullTime(data.opened_at)}>
          {relativeTime(data.opened_at, now)}
        </time>
        {data.time_to_resolve_seconds !== null && ` · resolved in ${duration(data.time_to_resolve_seconds)}`}
      </p>
      {incident.error && (
        <p className="table-banner" role="alert">
          Couldn't refresh: {incident.error.message}{" "}
          <button type="button" className="link-button" onClick={incident.reload}>
            Retry
          </button>
        </p>
      )}

      <div className="detail-grid">
        <div className="detail-main">
          <section className="card" aria-labelledby="analysis-heading">
            <h2 id="analysis-heading">Agent analysis</h2>
            <AnalysisBody incident={data} />
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
          <DecisionCard incident={data} onChanged={incident.reload} />
          <section className="card" aria-labelledby="timeline-heading">
            <h2 id="timeline-heading">Status</h2>
            <Timeline incident={data} />
          </section>
          <AlertCard incident={data} />
          <section className="card" aria-labelledby="linked-heading">
            <h2 id="linked-heading">Linked changes</h2>
            {data.linked_changes.length === 0 ? (
              <p className="muted">None. ConsoleTrack links a change here when the agent traces the alert back to it.</p>
            ) : (
              <ul className="linked-list">
                {data.linked_changes.map(({ change, confidence }) => (
                  <li key={change.id}>
                    <Link to={`/changes/${change.id}`}>
                      {changeLabel(change.id)}: {change.title}
                    </Link>
                    <span className="sub">
                      <span className="mono">{change.event_name}</span> · {STATUS_LABELS[change.status]} · {confidence}{" "}
                      confidence
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

function runSeconds(analysis: Analysis): number | null {
  const { started_at, finished_at } = analysis.agent_run;
  if (!finished_at) return null;
  return Math.max(0, Math.round((Date.parse(finished_at) - Date.parse(started_at)) / 1000));
}

function AnalysisBody({ incident }: { incident: Incident }) {
  const a = incident.analysis;
  if (incident.status === "triaging" && !a) {
    return (
      <p className="muted" role="status">
        The agent is triaging this incident. Its analysis appears here when the run finishes.
      </p>
    );
  }
  if (!a) {
    return <p className="muted">No analysis yet. An admin can re-run triage from the panel on the right.</p>;
  }
  const seconds = runSeconds(a);
  const run = a.agent_run;
  return (
    <div className="analysis">
      {incident.status === "triaging" && (
        <p className="notice" role="status">
          Re-running triage. This is the previous run's analysis until the new one finishes.
        </p>
      )}
      {a.flags.length > 0 && (
        <ul className="flags" aria-label="Flags">
          {a.flags.map((flag) => (
            <li key={flag} className={`pill ${WARNING_FLAGS.has(flag) ? "flag-warning" : "flag-info"}`}>
              {FLAG_LABELS[flag] ?? flag}
            </li>
          ))}
        </ul>
      )}
      <p>{a.summary}</p>
      {a.root_cause && (
        <p>
          <strong>Root cause.</strong> {a.root_cause}
        </p>
      )}
      {(a.risk || a.risk_reason) && (
        <p>
          <strong>Risk{a.risk ? `: ${RISK_LABELS[a.risk].toLowerCase()}` : ""}.</strong> {a.risk_reason}
        </p>
      )}
      <p className="analysis-meta">
        Confidence: {a.confidence} · {run.tool_calls} tool {run.tool_calls === 1 ? "call" : "calls"} ·{" "}
        <span className="mono">{run.model}</span>
        {seconds !== null && ` · ${duration(seconds)}`}
        {run.prompt_version && ` · prompt ${run.prompt_version}`}
        {run.escalated && " · escalated"}
        {run.outcome !== "succeeded" && run.outcome !== "running" && ` · run ${run.outcome.replace("_", " ")}`}
      </p>
    </div>
  );
}

// --- Decision: the proposed fix, Approve / Reject, Re-run ---------------------------------------

/** A message after an action; 501 means the backend can't do it yet. */
type Outcome = { kind: "error" | "unavailable" | "done"; text: string } | null;

function outcomeFor(err: unknown): Outcome {
  if (err instanceof ApiError && err.status === 501) {
    return { kind: "unavailable", text: `Not available yet: ${err.message}. Nothing was changed.` };
  }
  return { kind: "error", text: err instanceof Error ? err.message : String(err) };
}

function OutcomeNote({ outcome }: { outcome: Outcome }) {
  if (!outcome) return null;
  return (
    <p className={outcome.kind === "error" ? "form-error" : "notice"} role={outcome.kind === "error" ? "alert" : "status"}>
      {outcome.text}
    </p>
  );
}

function DecisionCard({ incident, onChanged }: { incident: Incident; onChanged: () => void }) {
  return (
    <section className="card" aria-labelledby="decision-heading">
      <h2 id="decision-heading">Proposed fix</h2>
      {incident.remediation ? (
        <RemediationPanel incident={incident} remediation={incident.remediation} onChanged={onChanged} />
      ) : (
        <p className="muted">
          {incident.analysis
            ? incident.analysis.confidence === "low"
              ? "No fix proposed: the agent's confidence is low. Check the evidence, or re-run triage with the escalation model."
              : "No fix proposed: the agent found no narrow, safe change to make."
            : "Nothing proposed yet."}
        </p>
      )}
      <RerunForm incident={incident} onChanged={onChanged} />
    </section>
  );
}

function RemediationPanel({
  incident,
  remediation: r,
  onChanged,
}: {
  incident: Incident;
  remediation: Remediation;
  onChanged: () => void;
}) {
  const { role } = useUser();
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const why = proposalReason(r);
  const link = safeHttpUrl(r.external_url);
  const decidable = r.status === "proposed" && incident.status === "awaiting_review";
  const applicable = APPLY_KINDS.includes(r.kind);
  const changed = valueChanges(r.before, r.after);

  const run = async (action: "approve" | "reject") => {
    setBusy(action);
    setOutcome(null);
    try {
      if (action === "approve") await approveRemediation(incident.id);
      else await rejectRemediation(incident.id, reason.trim());
      setOutcome({
        kind: "done",
        text:
          action === "approve"
            ? "Fix approved. The apply loop's steps appear below as it runs."
            : "Fix rejected; incident closed.",
      });
      onChanged();
    } catch (err) {
      setOutcome(outcomeFor(err));
      // 409: someone (or the policy gate) decided first; show what happened.
      if (err instanceof ApiError && err.status === 409) onChanged();
    } finally {
      setBusy(null);
    }
  };

  const onReject = (e: FormEvent) => {
    e.preventDefault();
    if (reason.trim()) void run("reject");
  };

  return (
    <div className="remediation">
      <p className="remediation-fix mono">{proposedFix(r)}</p>
      {why && <p className="remediation-reason">{why}</p>}
      <p className="muted small">
        <span className={`pill remediation-${r.status}`}>{REMEDIATION_STATUS_LABELS[r.status]}</span>
        {r.decided_by && ` · ${r.status === "rejected" ? "rejected" : "approved"} by ${r.decided_by}`}
        {r.applied_at && ` · applied ${relativeTime(r.applied_at)}`}
        {link && (
          <>
            {" · "}
            <a href={link} target="_blank" rel="noopener noreferrer">
              {r.kind === "codify_pr" ? "Pull request" : "Workflow run"}
            </a>
          </>
        )}
      </p>
      {r.mode && (
        <p className="gate small">
          <span className={`pill mode-${r.mode}`}>{r.mode === "auto" ? "Auto" : "Manual"}</span>
          {r.policy && (
            <>
              {" "}
              policy <span className="mono">{r.policy}</span>
            </>
          )}
          {r.gate_reason && <span className="muted">: {r.gate_reason}</span>}
        </p>
      )}
      {changed.length > 0 && (
        <ul className="value-changes mono small" aria-label="Changes">
          {changed.map((c) => (
            <li key={c.path}>
              {c.path} {c.before ?? "unset"} → {c.after ?? "unset"}
            </li>
          ))}
        </ul>
      )}
      {r.error && (
        <p className="form-error">
          <strong>Failed:</strong> {r.error}
        </p>
      )}
      {r.steps.length > 0 && (
        <ol className="apply-steps" aria-label="Steps">
          {r.steps.map((step, i) => (
            <li key={`${step.step}-${i}`} className={step.ok ? "ok" : "failed"}>
              <span className="apply-step-name mono">{step.step}</span>
              <span className="visually-hidden">{step.ok ? " (ok)" : " (failed)"}</span>
              {step.detail && <span className="apply-step-detail"> {step.detail}</span>}
              <time className="muted" dateTime={step.at} title={fullTime(step.at)}>
                {relativeTime(step.at)}
              </time>
            </li>
          ))}
        </ol>
      )}

      {decidable && (
        <>
          <AdminButton
            className="button button-accent full"
            disabled={busy !== null || !applicable}
            onClick={() => void run("approve")}
          >
            {busy === "approve" ? "Approving…" : "Approve fix"}
          </AdminButton>
          <p className="muted small">
            {applicable
              ? "The backend validates it against the live Deployment and the policy's bounds, does a server-side dry run, patches, and verifies; a failed verify rolls it back."
              : `Not available yet: only ${APPLY_KINDS.join(", ")} fixes can be applied. Reject it, or fix it by hand.`}
          </p>
          <form className="reject-form" onSubmit={onReject}>
            <label htmlFor={reasonId}>Reason (required to reject)</label>
            <textarea
              id={reasonId}
              rows={3}
              maxLength={2000}
              placeholder="Why this fix is wrong"
              value={reason}
              disabled={role !== "admin"}
              onChange={(e) => setReason(e.target.value)}
            />
            <AdminButton
              type="submit"
              className="button button-secondary full"
              disabled={busy !== null || !reason.trim()}
            >
              {busy === "reject" ? "Rejecting…" : "Reject and close"}
            </AdminButton>
          </form>
        </>
      )}
      <OutcomeNote outcome={outcome} />
    </div>
  );
}

function RerunForm({ incident, onChanged }: { incident: Incident; onChanged: () => void }) {
  const modelId = useId();
  const hintId = useId();
  const { role } = useUser();
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const allowed = RERUNNABLE.includes(incident.status);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!allowed) return;
    setBusy(true);
    setOutcome(null);
    try {
      await triageIncident(incident.id, model.trim() || null);
      setOutcome({ kind: "done", text: "Triage started. The new analysis appears here when it finishes." });
      onChanged();
    } catch (err) {
      setOutcome(outcomeFor(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="rerun-form" onSubmit={(e) => void onSubmit(e)}>
      <h3>Re-run triage</h3>
      <label htmlFor={modelId}>Model (optional)</label>
      <input
        id={modelId}
        className="mono"
        maxLength={128}
        placeholder="Default triage model"
        value={model}
        disabled={role !== "admin" || !allowed}
        aria-describedby={hintId}
        onChange={(e) => setModel(e.target.value)}
      />
      <p id={hintId} className="muted small">
        {allowed
          ? "Leave empty for the default, or name the configured escalation model."
          : `Available when the incident is open or awaiting review, not ${INCIDENT_STATUS_LABELS[incident.status].toLowerCase()}.`}
      </p>
      <AdminButton type="submit" className="button button-secondary full" disabled={busy || !allowed}>
        {busy ? "Starting…" : "Re-run triage"}
      </AdminButton>
      <OutcomeNote outcome={outcome} />
    </form>
  );
}

// --- Timeline and alert --------------------------------------------------------------------------

function Timeline({ incident }: { incident: Incident }) {
  const steps = timelineSteps(incident);
  const current = steps.findIndex((s) => s.status === incident.status);
  return (
    <ol className="timeline">
      {steps.map((step, index) => {
        const state = index < current ? "done" : index === current ? "current" : "upcoming";
        // A resolved webhook can skip steps (e.g. awaiting_review → resolved): show them as skipped.
        const skipped = state === "done" && !step.at && index > 0;
        return (
          <li key={step.status} className={`timeline-step ${state}`} aria-current={state === "current" ? "step" : undefined}>
            <span className="timeline-dot" aria-hidden="true" />
            <span className="timeline-label">{step.label}</span>
            <span className="timeline-time muted small">
              {step.at ? (
                <time dateTime={step.at} title={fullTime(step.at)}>
                  {relativeTime(step.at)}
                </time>
              ) : skipped ? (
                "skipped"
              ) : state === "current" ? (
                "now"
              ) : (
                ""
              )}
            </span>
            <span className="visually-hidden">
              {state === "done" ? " (done)" : state === "current" ? " (current)" : " (not yet)"}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function AlertCard({ incident }: { incident: Incident }) {
  const labels = Object.entries(incident.labels);
  const annotations = Object.entries(incident.annotations);
  return (
    <section className="card" aria-labelledby="alert-heading">
      <h2 id="alert-heading">Alert</h2>
      <dl className="facts">
        <dt>Name</dt>
        <dd className="mono">{incident.alertname}</dd>
        <dt>Started</dt>
        <dd>
          <time dateTime={incident.opened_at}>{fullTime(incident.opened_at)}</time>
        </dd>
        {annotations.map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{value}</dd>
          </div>
        ))}
        <dt>Labels</dt>
        <dd>
          {labels.length === 0 ? (
            <span className="muted">None</span>
          ) : (
            <ul className="labels" aria-label="Labels">
              {labels.map(([key, value]) => (
                <li key={key} className="mono">
                  {key}={value}
                </li>
              ))}
            </ul>
          )}
        </dd>
        <dt>Fingerprint</dt>
        <dd className="mono">{incident.fingerprint}</dd>
      </dl>
    </section>
  );
}
