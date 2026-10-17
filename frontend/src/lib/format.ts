import type { ChangeStatus, IncidentRef, IntakeState, Risk, Via } from "../api";

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

export const RISK_LABELS: Record<Risk, string> = { low: "Low", medium: "Medium", high: "High" };

export const VIA_LABELS: Record<Via, string> = {
  console: "console",
  cli: "cli",
  cloudshell: "cloudshell",
  sdk: "sdk",
  terraform_outside_pipeline: "terraform, outside pipeline",
};

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
