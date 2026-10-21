import type { Incident, IncidentStatus } from "../../api";

export interface Step {
  status: IncidentStatus;
  label: string;
  at: string | null;
}

/**
 * open → triaging → awaiting_review → remediating → resolved, or → closed. The API has no status
 * history, so times come from the incident, its latest agent run, and its remediation.
 */
export function timelineSteps(incident: Incident): Step[] {
  const run = incident.status === "triaging" ? null : incident.analysis?.agent_run;
  const steps: Step[] = [
    { status: "open", label: "Opened", at: incident.opened_at },
    { status: "triaging", label: "Triaging", at: run?.started_at ?? null },
    { status: "awaiting_review", label: "Awaiting review", at: run?.finished_at ?? null },
  ];
  if (incident.status === "closed") {
    steps.push({ status: "closed", label: "Closed", at: null });
  } else {
    // The incident moves to remediating when the fix is approved (by an admin or the policy gate).
    const r = incident.remediation;
    steps.push({ status: "remediating", label: "Remediating", at: r?.decided_at ?? r?.applied_at ?? null });
    steps.push({ status: "resolved", label: "Resolved", at: incident.resolved_at });
  }
  return steps;
}
