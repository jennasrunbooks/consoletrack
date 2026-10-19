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
export type Risk = Schemas["Risk"];
export type Via = Schemas["Via"];
export type IncidentRef = Schemas["IncidentRef"];
export type IntakeHealth = Schemas["IntakeHealth"];
export type IntakeState = Schemas["IntakeState"];
export type ErrorBody = Schemas["Error"];
export type ListChangesQuery = NonNullable<paths["/api/v1/changes"]["get"]["parameters"]["query"]>;

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

// --- Intake -----------------------------------------------------------------------------------

export function getIntakeHealth(signal?: AbortSignal): Promise<IntakeHealth> {
  return unwrap(client.GET("/api/v1/intake/health", { signal }));
}
