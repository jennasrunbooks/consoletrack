// MSW handlers for every endpoint PR 1 uses, shared by tests (msw/node) and `npm run dev:mock`
// (msw/browser). They follow openapi.yaml: the same filters, the `counts` rules, and `Error`
// bodies, so the UI can't lean on behavior the backend won't have.
import { HttpResponse, http, type PathParams } from "msw";
import type {
  ChangeCounts,
  ChangePage,
  ChangeStatus,
  ChangeSummary,
  ErrorBody,
  IntakeHealth,
  Risk,
  User,
  Via,
} from "../api";
import { buildChanges, buildIntakeHealth, mockUsers } from "./fixtures";

const API = "*/api/v1";

/** A handler's response body: the success schema or an `Error`. */
type Body<T> = T | ErrorBody;

export const CHANGE_STATUSES: ChangeStatus[] = [
  "detected",
  "awaiting_review",
  "reverting",
  "reverted",
  "accepted",
  "codifying",
  "codified",
  "failed",
];
const CLOSED: ChangeStatus[] = ["reverted", "accepted", "codified"];
const RISKS: Risk[] = ["low", "medium", "high"];
const VIAS: Via[] = ["console", "cli", "cloudshell", "sdk", "terraform_outside_pipeline"];

/** The mock's session: who is logged in. Tests reset it; the browser worker persists it. */
export const mockSession = {
  user: null as User | null,
  onChange: null as ((user: User | null) => void) | null,
  set(user: User | null) {
    this.user = user;
    this.onChange?.(user);
  },
};

/** The mock's data, replaceable per test. */
export const mockDb = {
  changes: buildChanges(),
  intake: buildIntakeHealth() as IntakeHealth,
  reset() {
    this.changes = buildChanges();
    this.intake = buildIntakeHealth();
  },
};

export const errorResponse = (status: number, code: string, message: string, init: ResponseInit = {}) =>
  HttpResponse.json<ErrorBody>({ code, message }, { status, ...init });

const unauthorized = () => errorResponse(401, "unauthorized", "Log in to continue.");

function validationError(field: string, msg: string) {
  return HttpResponse.json<ErrorBody>(
    {
      code: "validation_error",
      message: "Request validation failed.",
      details: [{ loc: ["query", field], msg, type: "value_error" }],
    },
    { status: 422 },
  );
}

function enumValues<T extends string>(params: URLSearchParams, name: string, allowed: readonly T[]): T[] | string {
  const values = params.getAll(name);
  const bad = values.find((v) => !allowed.includes(v as T));
  return bad === undefined ? (values as T[]) : bad;
}

function countsFor(changes: ChangeSummary[], now: number): ChangeCounts {
  const byStatus = Object.fromEntries(CHANGE_STATUSES.map((s) => [s, 0])) as Record<ChangeStatus, number>;
  for (const c of changes) byStatus[c.status] += 1;
  const weekAgo = now - 7 * 24 * 3600_000;
  return {
    by_status: byStatus,
    total: changes.length,
    awaiting_review: byStatus.awaiting_review,
    high_risk_open: changes.filter((c) => c.risk === "high" && !CLOSED.includes(c.status)).length,
    linked_to_incident: changes.filter((c) => c.linked_incidents.length > 0).length,
    // The backend counts by decision time; event time is close enough for sample data.
    closed_last_7_days: changes.filter((c) => CLOSED.includes(c.status) && Date.parse(c.event_time) >= weekAgo)
      .length,
  };
}

export const handlers = [
  http.post<PathParams, never, Body<User>>(`${API}/auth/login`, async ({ request }) => {
    const body = (await request.json().catch(() => null)) as { username?: string; password?: string } | null;
    if (!body?.username || !body.password) return validationError("password", "Field required");
    const account = mockUsers[body.username];
    if (!account || account.password !== body.password) {
      return errorResponse(401, "unauthorized", "Invalid username or password.");
    }
    mockSession.set(account.user);
    return HttpResponse.json<User>(account.user);
  }),

  http.post<PathParams, never, Body<null>>(`${API}/auth/logout`, () => {
    if (!mockSession.user) return unauthorized();
    mockSession.set(null);
    return new HttpResponse(null, { status: 204 });
  }),

  http.get<PathParams, never, Body<User>>(`${API}/auth/me`, () => (mockSession.user ? HttpResponse.json<User>(mockSession.user) : unauthorized())),

  http.get<PathParams, never, Body<ChangePage>>(`${API}/changes`, ({ request }) => {
    if (!mockSession.user) return unauthorized();
    const params = new URL(request.url).searchParams;

    const status = enumValues(params, "status", CHANGE_STATUSES);
    if (typeof status === "string") return validationError("status", `Invalid status: ${status}`);
    const risk = enumValues(params, "risk", RISKS);
    if (typeof risk === "string") return validationError("risk", `Invalid risk: ${risk}`);
    const via = enumValues(params, "via", VIAS);
    if (typeof via === "string") return validationError("via", `Invalid via: ${via}`);
    const service = params.get("service");
    if (service && !/^[a-z0-9-]+$/.test(service)) return validationError("service", "Invalid service name");
    const page = Number(params.get("page") ?? 1);
    const pageSize = Number(params.get("page_size") ?? 25);
    if (!Number.isInteger(page) || page < 1) return validationError("page", "Must be at least 1");
    if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) {
      return validationError("page_size", "Must be between 1 and 100");
    }

    const principal = params.get("principal")?.toLowerCase();
    const since = params.get("since");
    const until = params.get("until");
    const q = params.get("q")?.toLowerCase();

    // Every filter but status; `counts` is computed here (openapi.yaml → ChangeCounts).
    const filtered = mockDb.changes
      .filter((c) => risk.length === 0 || (c.risk !== null && risk.includes(c.risk)))
      .filter((c) => via.length === 0 || via.includes(c.via))
      .filter((c) => !service || c.service === service)
      .filter(
        (c) =>
          !principal ||
          c.principal_arn.toLowerCase().includes(principal) ||
          (c.session_name ?? "").toLowerCase().includes(principal),
      )
      .filter((c) => !since || Date.parse(c.event_time) >= Date.parse(since))
      .filter((c) => !until || Date.parse(c.event_time) < Date.parse(until))
      .filter(
        (c) =>
          !q ||
          [c.title, c.resource_name ?? "", c.resource_arn, c.principal_arn, c.session_name ?? "", c.event_name].some(
            (field) => field.toLowerCase().includes(q),
          ),
      )
      .sort((a, b) => Date.parse(b.event_time) - Date.parse(a.event_time));
    const matching = filtered.filter((c) => status.length === 0 || status.includes(c.status));
    const start = (page - 1) * pageSize;

    return HttpResponse.json<ChangePage>({
      items: matching.slice(start, start + pageSize),
      total: matching.length,
      page,
      page_size: pageSize,
      counts: countsFor(filtered, Date.now()),
    });
  }),

  http.get<PathParams, never, Body<IntakeHealth>>(`${API}/intake/health`, () =>
    mockSession.user ? HttpResponse.json<IntakeHealth>(mockDb.intake) : unauthorized(),
  ),
];
