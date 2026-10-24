import type {
  AnalysisFlag,
  Evidence,
  ChangeStatus,
  IncidentRef,
  IncidentStatus,
  IntakeState,
  Remediation,
  RemediationStatus,
  Risk,
  Via,
} from "../api";

/** 42 → "CHG-0042" (openapi.yaml → ChangeSummary). */
export const changeLabel = (id: number): string => `CHG-${String(id).padStart(4, "0")}`;

/** 12 → "INC-12". */
export const incidentLabel = (id: number): string => `INC-${id}`;

export const incidentTitle = (incident: IncidentRef): string =>
  `${incidentLabel(incident.id)}: ${[incident.workload, incident.alertname].filter(Boolean).join(" ")}`;

export const STATUS_LABELS: Record<ChangeStatus, string> = {
  detected: "Detected",
  awaiting_review: "Awaiting review",
  reverting: "Reverting",
  reverted: "Reverted",
  accepted: "Accepted",
  codifying: "Codifying",
  codified: "Codified",
  failed: "Failed",
};

export const INCIDENT_STATUS_LABELS: Record<IncidentStatus, string> = {
  open: "Open",
  triaging: "Triaging",
  awaiting_review: "Awaiting review",
  remediating: "Remediating",
  resolved: "Resolved",
  closed: "Closed",
};

export const REMEDIATION_STATUS_LABELS: Record<RemediationStatus, string> = {
  proposed: "Proposed",
  approved: "Approved",
  rejected: "Rejected",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
};

/** The agent's flags (openapi.yaml → AnalysisFlag). */
export const FLAG_LABELS: Record<AnalysisFlag, string> = {
  possible_prompt_injection: "Possible prompt injection",
  aws_evidence_unavailable: "AWS evidence unavailable",
  cloudtrail_delay: "CloudTrail delay",
  insufficient_evidence: "Insufficient evidence",
  stateful_workload: "Stateful workload",
  migration: "Migration",
};

/** Flags a reviewer must see before trusting the analysis; shown in the warning color. */
export const WARNING_FLAGS: ReadonlySet<AnalysisFlag> = new Set([
  "possible_prompt_injection",
  "aws_evidence_unavailable",
  "insufficient_evidence",
]);

export const RISK_LABELS: Record<Risk, string> = { low: "Low", medium: "Medium", high: "High" };

export const VIA_LABELS: Record<Via, string> = {
  console: "console",
  cli: "cli",
  cloudshell: "cloudshell",
  sdk: "sdk",
  terraform_outside_pipeline: "terraform, outside pipeline",
};

/** Where a change was made, for the change detail page's facts (openapi.yaml → Via). */
export const VIA_DETAIL: Record<Via, string> = {
  console: "AWS console",
  cli: "AWS CLI",
  cloudshell: "AWS CloudShell",
  sdk: "AWS SDK",
  terraform_outside_pipeline: "Terraform, outside the pipeline",
};

/** "made in the AWS console", for the line under a change's title. */
export const VIA_PHRASE: Record<Via, string> = {
  console: "made in the AWS console",
  cli: "made with the AWS CLI",
  cloudshell: "made in AWS CloudShell",
  sdk: "made with an AWS SDK",
  terraform_outside_pipeline: "made with Terraform outside the pipeline",
};

/**
 * The resource part of an ARN, for display: `…:security-group/sg-0a1b` → `sg-0a1b`,
 * `arn:aws:s3:::demo-app-data` → `demo-app-data`, `…:role/demo-app/demo-app` → `role/demo-app/demo-app`.
 */
export function resourceId(arn: string): string {
  const resource = arn.split(":").slice(5).join(":");
  if (!resource) return arn;
  const [type, ...rest] = resource.split("/");
  if (rest.length === 0) return resource;
  return type === "role" || type === "user" || type === "policy" ? resource : rest.join("/");
}

/** A UTC timestamp as "Oct 14, 2026 14:52:07 UTC", the way CloudTrail reports it. */
export function utcTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const day = date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  return `${day} ${date.toISOString().slice(11, 19)} UTC`;
}

export const INTAKE_LABELS: Record<IntakeState, string> = {
  live: "Live",
  delayed: "Delayed",
  down: "Down",
  replay: "Replay",
};

/**
 * Who made the change, as two lines: the person (session name) and where their access came
 * from (role, SSO permission set, or IAM user).
 *   assumed-role/ct-demo-developer/jdev                       → jdev · ct-demo-developer
 *   assumed-role/AWSReservedSSO_AdministratorAccess_0f1e…/js  → js · AdministratorAccess (SSO)
 *   user/alice                                                → alice · IAM user
 */
export function principalLabel(principalArn: string, sessionName: string | null): { who: string; via: string } {
  const resource = principalArn.split(":").slice(5).join(":");
  const [kind, ...rest] = resource.split("/");
  if (kind === "assumed-role" && rest.length >= 1) {
    const role = rest[0] ?? "";
    const sso = /^AWSReservedSSO_(.+)_[0-9a-f]+$/.exec(role);
    const roleLabel = sso ? `${sso[1]} (SSO)` : role;
    return { who: sessionName ?? rest[1] ?? roleLabel, via: roleLabel };
  }
  if (kind === "user") return { who: sessionName ?? rest.at(-1) ?? principalArn, via: "IAM user" };
  if (kind === "root") return { who: "root", via: "Account root user" };
  return { who: sessionName ?? (resource || principalArn), via: rest.at(-1) ?? kind ?? "" };
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now", "4 min ago", "1 h ago", "Yesterday", "Oct 12", or "Oct 12, 2025" for older years. */
export function relativeTime(iso: string, now = Date.now()): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const diff = now - then;
  if (diff < MINUTE) return "just now";
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} min ago`;
  const date = new Date(then);
  const today = new Date(now);
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  if (then >= startOfToday || diff < 6 * HOUR) return `${Math.floor(diff / HOUR)} h ago`;
  if (then >= startOfToday - DAY) return "Yesterday";
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() === today.getFullYear() ? {} : { year: "numeric" }),
  });
}

/** Seconds as "0 s", "45 s", "12 min", "3 h". */
export function duration(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} h`;
}

/** Full timestamp for a `title` tooltip. */
export const fullTime = (iso: string): string => new Date(iso).toLocaleString("en-US", { timeZoneName: "short" });

/** The URL if it is http(s), else null, so a link can never carry a javascript: URL. */
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

const str = (value: unknown): string | null => (typeof value === "string" && value !== "" ? value : null);

/**
 * A remediation's payload as one line (openapi.yaml → Remediation.payload), e.g.
 *   set_memory_limit demo/demo-app container demo-app → 256Mi
 * The payload is the agent's output, so it is only ever shown as text.
 */
/** The agent's `reason` from a proposal payload, if it gave one. */
export const proposalReason = (remediation: Pick<Remediation, "payload">): string | null =>
  str(remediation.payload.reason);

export function proposedFix(remediation: Pick<Remediation, "kind" | "payload">): string {
  const p = remediation.payload;
  const target = [str(p.namespace), str(p.deployment)].filter(Boolean).join("/");
  switch (remediation.kind) {
    case "set_memory_limit":
      return `set_memory_limit ${target} container ${str(p.container) ?? "?"} → ${str(p.memory_limit) ?? "?"}`;
    case "rollback":
      return `rollback ${target} to ${typeof p.to_revision === "number" ? `revision ${p.to_revision}` : "the previous revision"}`;
    case "revert_workflow":
      return `revert_workflow ${typeof p.change_id === "number" ? changeLabel(p.change_id) : "?"}`;
    default:
      return `${remediation.kind} ${target}`.trim();
  }
}

/** `get_pods(namespace="demo", tail=50)`: a tool call as one line of text. */
export function callSignature(e: Pick<Evidence, "tool_name" | "args">): string {
  const args = Object.entries(e.args).map(([key, value]) => `${key}=${JSON.stringify(value)}`);
  return `${e.tool_name}(${args.join(", ")})`;
}

/**
 * A tool result for display: the worker stores k8s-mcp's structured output as compact JSON, so
 * pretty-print it when it parses. Either way the caller renders it as text.
 */
export function readableResult(excerpt: string): string {
  try {
    return JSON.stringify(JSON.parse(excerpt), null, 2);
  } catch {
    return excerpt; // not JSON, or cut off at the 8,000-character excerpt limit
  }
}
