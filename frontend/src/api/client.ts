// The only module that talks to the backend (AGENTS.md → Frontend). Paths, parameters, and
// bodies are typed from schema.d.ts, which `npm run gen:api` generates from ../openapi.yaml.
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type User = Schemas["User"];
export type Role = Schemas["Role"];
export type ChangeSummary = Schemas["ChangeSummary"];
export type ChangePage = Schemas["ChangePage"];
export type ChangeCounts = Schemas["ChangeCounts"];
export type ChangeStatus = Schemas["ChangeStatus"];
export type Change = Schemas["Change"];
export type ChangeDecision = Schemas["ChangeDecision"];
export type Drift = Schemas["Drift"];
export type DriftLine = Schemas["DriftLine"];
export type LinkedIncident = Schemas["LinkedIncident"];
export type RevertResponse = Schemas["RevertResponse"];
export type Risk = Schemas["Risk"];
export type Via = Schemas["Via"];
export type IncidentRef = Schemas["IncidentRef"];
export type IncidentSummary = Schemas["IncidentSummary"];
export type IncidentPage = Schemas["IncidentPage"];
export type Incident = Schemas["Incident"];
export type IncidentStatus = Schemas["IncidentStatus"];
export type ChangeRef = Schemas["ChangeRef"];
export type Analysis = Schemas["Analysis"];
export type AgentRun = Schemas["AgentRun"];
export type AnalysisFlag = Schemas["AnalysisFlag"];
export type Confidence = Schemas["Confidence"];
export type Evidence = Schemas["Evidence"];
export type EvidenceList = Schemas["EvidenceList"];
export type Remediation = Schemas["Remediation"];
export type RemediationKind = Schemas["RemediationKind"];
export type RemediationStatus = Schemas["RemediationStatus"];
export type SubjectPath = Schemas["SubjectPath"];
export type IntakeHealth = Schemas["IntakeHealth"];
export type IntakeState = Schemas["IntakeState"];
export type ErrorBody = Schemas["Error"];
export type ListChangesQuery = NonNullable<paths["/api/v1/changes"]["get"]["parameters"]["query"]>;
export type ListIncidentsQuery = NonNullable<paths["/api/v1/incidents"]["get"]["parameters"]["query"]>;

/** Every non-2xx response, carrying the `Error` body when the backend sent one. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ErrorBody["details"];
  /** Seconds from `Retry-After` (sent with 429), if any. */
  readonly retryAfter: number | null;

  constructor(status: number, body: Partial<ErrorBody> | undefined, retryAfter: number | null = null) {
    super(body?.message ?? `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code ?? "http_error";
    this.details = body?.details;
    this.retryAfter = retryAfter;
  }
}

// Same origin in every deployment (nginx or the Vite proxy in front of /api), so the session
// cookie (HttpOnly, SameSite=Strict) goes along with `same-origin`; nothing reads or stores it.
// The absolute base keeps request URLs valid under Node in tests.
const client = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  credentials: "same-origin",
  headers: { Accept: "application/json" },
  // Look fetch up on each call: openapi-fetch otherwise keeps the one present at import time,
  // which MSW (tests and dev:mock) replaces later.
  fetch: (request) => globalThis.fetch(request),
});

type UnauthorizedListener = () => void;
let onUnauthorized: UnauthorizedListener | null = null;

/** Called on a 401 from any call except login and /auth/me, i.e. when a session expires. */
export function setUnauthorizedListener(listener: UnauthorizedListener | null): void {
  onUnauthorized = listener;
}

async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
  { notifyUnauthorized = true } = {},
): Promise<T> {
  let result: Awaited<typeof call>;
  try {
    result = await call;
  } catch (err) {
    // Network failure or the request was aborted: no response at all.
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, { code: "network_error", message: "Can't reach ConsoleTrack. Check your connection." });
  }
  const { data, error, response } = result;
  if (response.ok) return data as T;
  if (response.status === 401 && notifyUnauthorized) onUnauthorized?.();
  const body = isErrorBody(error) ? error : undefined;
  const retryAfter = Number.parseInt(response.headers.get("Retry-After") ?? "", 10);
  throw new ApiError(response.status, body, Number.isFinite(retryAfter) ? retryAfter : null);
}

function isErrorBody(value: unknown): value is ErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as ErrorBody).code === "string" &&
    typeof (value as ErrorBody).message === "string"
  );
}

// --- Auth -------------------------------------------------------------------------------------

export function login(username: string, password: string): Promise<User> {
  return unwrap(client.POST("/api/v1/auth/login", { body: { username, password } }), {
    notifyUnauthorized: false,
  });
}

export async function logout(): Promise<void> {
  await unwrap(client.POST("/api/v1/auth/logout"), { notifyUnauthorized: false });
}

/** The logged-in user, or null when there is no valid session. */
export async function getCurrentUser(signal?: AbortSignal): Promise<User | null> {
  try {
    return await unwrap(client.GET("/api/v1/auth/me", { signal }), { notifyUnauthorized: false });
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}

// --- Changes ----------------------------------------------------------------------------------

export function listChanges(query: ListChangesQuery, signal?: AbortSignal): Promise<ChangePage> {
  return unwrap(client.GET("/api/v1/changes", { params: { query }, signal }));
}

export function getChange(id: number, signal?: AbortSignal): Promise<Change> {
  return unwrap(client.GET("/api/v1/changes/{id}", { params: { path: { id } }, signal }));
}

/** Dispatch the gated revert workflow (202); the change moves to `reverting`. */
export function revertChange(id: number, note: string | null = null): Promise<RevertResponse> {
  return unwrap(client.POST("/api/v1/changes/{id}/revert", { params: { path: { id } }, body: { note } }));
}

/** Close the change as `accepted`; the note is required (openapi.yaml → AcceptRequest). */
export function acceptChange(id: number, note: string): Promise<Change> {
  return unwrap(client.POST("/api/v1/changes/{id}/accept", { params: { path: { id } }, body: { note } }));
}

// --- Incidents --------------------------------------------------------------------------------

export function listIncidents(query: ListIncidentsQuery, signal?: AbortSignal): Promise<IncidentPage> {
  return unwrap(client.GET("/api/v1/incidents", { params: { query }, signal }));
}

export function getIncident(id: number, signal?: AbortSignal): Promise<Incident> {
  return unwrap(client.GET("/api/v1/incidents/{id}", { params: { path: { id } }, signal }));
}

/** Re-run triage; `model` null uses the configured default (TRIAGE_MODEL). */
export function triageIncident(id: number, model: string | null): Promise<Incident> {
  return unwrap(client.POST("/api/v1/incidents/{id}/triage", { params: { path: { id } }, body: { model } }));
}

export function approveRemediation(id: number, note: string | null = null): Promise<Incident> {
  return unwrap(
    client.POST("/api/v1/incidents/{id}/remediation/approve", { params: { path: { id } }, body: { note } }),
  );
}

export function rejectRemediation(id: number, reason: string): Promise<Incident> {
  return unwrap(
    client.POST("/api/v1/incidents/{id}/remediation/reject", { params: { path: { id } }, body: { reason } }),
  );
}

/** Replace the postmortem Markdown on the incident's latest analysis. */
export function savePostmortem(id: number, postmortemMd: string): Promise<Analysis> {
  return unwrap(
    client.PUT("/api/v1/incidents/{id}/postmortem", { params: { path: { id } }, body: { postmortem_md: postmortemMd } }),
  );
}

// --- Evidence ---------------------------------------------------------------------------------

/** Every agent run's tool calls for a change or an incident, oldest first, already redacted. */
export function listEvidence(subject: SubjectPath, id: number, signal?: AbortSignal): Promise<EvidenceList> {
  return unwrap(client.GET("/api/v1/{subject}/{id}/evidence", { params: { path: { subject, id } }, signal }));
}

// --- Intake -----------------------------------------------------------------------------------

export function getIntakeHealth(signal?: AbortSignal): Promise<IntakeHealth> {
  return unwrap(client.GET("/api/v1/intake/health", { signal }));
}
