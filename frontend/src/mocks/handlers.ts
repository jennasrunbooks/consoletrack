// MSW handlers for every endpoint the UI uses, shared by tests (msw/node) and `npm run dev:mock`
// (msw/browser). They follow openapi.yaml: the same filters, the `counts` rules, and `Error`
// bodies, so the UI can't lean on behavior the backend won't have.
import { HttpResponse, http, type PathParams } from "msw";
import type {
  ChangeCounts,
  ChangePage,
  ChangeStatus,
  ChangeSummary,
  ErrorBody,
  EvidenceList,
  Incident,
  IncidentPage,
  IncidentStatus,
  IncidentSummary,
  IntakeHealth,
  Remediation,
  Risk,
  User,
  Via,
} from "../api";
import {
  buildChanges,
  buildEvidence,
  buildIncidents,
  buildIntakeHealth,
  finishedTriage,
  mockUsers,
} from "./fixtures";

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
export const INCIDENT_STATUSES: IncidentStatus[] = [
  "open",
  "triaging",
  "awaiting_review",
  "remediating",
  "resolved",
  "closed",
];
/** TRIAGE_MODEL and TRIAGE_MODEL_ESCALATION from .env.example; the backend rejects any other. */
export const TRIAGE_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5"];
/** How long a mock triage run takes before its analysis appears. */
export const MOCK_TRIAGE_MS = 6_000;
/** How long the mock apply loop takes from approval to a verified fix. */
export const MOCK_APPLY_MS = 6_000;
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
  incidents: buildIncidents(),
  evidence: buildEvidence(),
  /** When each triaging incident's run finishes (ms since epoch), with the model it runs on. */
  triage: initialTriage(),
  /** When each approved fix finishes the apply loop (ms since epoch). */
  apply: {} as Record<number, number>,
  reset() {
    this.changes = buildChanges();
    this.intake = buildIntakeHealth();
    this.incidents = buildIncidents();
    this.evidence = buildEvidence();
    this.triage = initialTriage();
    this.apply = {};
  },
};

function initialTriage(): Record<number, { doneAt: number; model: string }> {
  return { 15: { doneAt: Date.now() + MOCK_TRIAGE_MS, model: TRIAGE_MODELS[0]! } };
}

/** Finishes a triage run whose time is up, the way the worker would. */
function settleTriage(incident: Incident): Incident {
  const run = mockDb.triage[incident.id];
  if (incident.status !== "triaging" || !run || Date.now() < run.doneAt) return incident;
  delete mockDb.triage[incident.id];
  const runId = Math.max(0, ...mockDb.incidents.map((i) => i.analysis?.agent_run.id ?? 0)) + 1;
  const fresh = finishedTriage(incident.id, runId, run.model);
  // A re-run keeps the earlier conclusions in the mock; a first run gets the canned analysis.
  const analysis = incident.analysis
    ? { ...incident.analysis, id: runId, agent_run: { ...fresh.agent_run } }
    : fresh;
  Object.assign(incident, { status: "awaiting_review", analysis } satisfies Partial<Incident>);
  return incident;
}

/**
 * Finishes an approved set_memory_limit fix whose time is up, the way the backend's apply loop
 * would. The incident stays `remediating` until Alertmanager's resolved notification.
 */
function settleApply(incident: Incident): Incident {
  const doneAt = mockDb.apply[incident.id];
  const r = incident.remediation;
  if (doneAt === undefined || !r || Date.now() < doneAt) return incident;
  delete mockDb.apply[incident.id];
  const limit = typeof r.payload.memory_limit === "string" ? r.payload.memory_limit : "?";
  const at = new Date().toISOString();
  const summary = `memory limit 128Mi → ${limit}`;
  Object.assign(r, {
    status: "succeeded",
    applied_at: at,
    before: { limits: { memory: "128Mi" }, requests: { memory: "128Mi" } },
    after: { limits: { memory: limit } },
    steps: [
      ...r.steps,
      ...(
        [
          ["claimed", ""],
          ["read", `${String(r.payload.namespace)}/${String(r.payload.deployment)}`],
          ["validate", summary],
          ["dry_run", "server-side dry run accepted the patch"],
          ["patch", summary],
          ["rollout", "every replica runs the new limit"],
          ["verify", "error ratio 0.000 is below 0.05"],
          ["succeeded", "error ratio 0.000 is below 0.05"],
        ] as const
      ).map(([step, detail]) => ({ step, at, ok: true, detail })),
    ],
  } satisfies Partial<Remediation>);
  return incident;
}

const toSummary = (i: Incident): IncidentSummary => ({
  id: i.id,
  alertname: i.alertname,
  severity: i.severity,
  namespace: i.namespace,
  workload: i.workload,
  status: i.status,
  opened_at: i.opened_at,
  resolved_at: i.resolved_at,
  linked_changes: i.linked_changes.map((l) => l.change),
});

export const errorResponse = (status: number, code: string, message: string, init: ResponseInit = {}) =>
  HttpResponse.json<ErrorBody>({ code, message }, { status, ...init });

const unauthorized = () => errorResponse(401, "unauthorized", "Log in to continue.");
const forbidden = () => errorResponse(403, "forbidden", "This action requires the admin role.");
const notFound = (what: string) => errorResponse(404, "not_found", `${what} not found`);

/** Null when the session may write, else the 401 or 403 the backend would send. */
const adminCheck = () => (!mockSession.user ? unauthorized() : mockSession.user.role !== "admin" ? forbidden() : null);

function findIncident(id: string | readonly string[] | undefined): Incident | undefined {
  const incident = mockDb.incidents.find((i) => String(i.id) === id);
  return incident && settleApply(settleTriage(incident));
}

const noProposal = (id: number) =>
  errorResponse(409, "no_proposed_remediation", `incident ${id} has no proposed remediation to decide on`);

function validationError(field: string, msg: string, where: "query" | "body" = "query") {
  return HttpResponse.json<ErrorBody>(
    {
      code: "validation_error",
      message: "Request validation failed.",
      details: [{ loc: [where, field], msg, type: "value_error" }],
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

  http.get<PathParams, never, Body<IncidentPage>>(`${API}/incidents`, ({ request }) => {
    if (!mockSession.user) return unauthorized();
    const params = new URL(request.url).searchParams;
    const status = enumValues(params, "status", INCIDENT_STATUSES);
    if (typeof status === "string") return validationError("status", `Invalid status: ${status}`);
    const severity = params.get("severity");
    const namespace = params.get("namespace");
    const page = Number(params.get("page") ?? 1);
    const pageSize = Number(params.get("page_size") ?? 25);
    if (!Number.isInteger(page) || page < 1) return validationError("page", "Must be at least 1");
    if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) {
      return validationError("page_size", "Must be between 1 and 100");
    }
    const matching = mockDb.incidents
      .map((i) => settleApply(settleTriage(i)))
      .filter((i) => status.length === 0 || status.includes(i.status))
      .filter((i) => !severity || i.severity === severity)
      .filter((i) => !namespace || i.namespace === namespace)
      .sort((a, b) => Date.parse(b.opened_at) - Date.parse(a.opened_at));
    const start = (page - 1) * pageSize;
    return HttpResponse.json<IncidentPage>({
      items: matching.slice(start, start + pageSize).map(toSummary),
      total: matching.length,
      page,
      page_size: pageSize,
    });
  }),

  http.get<{ id: string }, never, Body<Incident>>(`${API}/incidents/:id`, ({ params }) => {
    if (!mockSession.user) return unauthorized();
    const incident = findIncident(params.id);
    return incident ? HttpResponse.json<Incident>(incident) : notFound(`incident ${params.id}`);
  }),

  http.post<{ id: string }, { model?: string | null } | null, Body<Incident>>(
    `${API}/incidents/:id/triage`,
    async ({ params, request }) => {
      const denied = adminCheck();
      if (denied) return denied;
      const incident = findIncident(params.id);
      if (!incident) return notFound(`incident ${params.id}`);
      const body = await request.json().catch(() => null);
      const model = body?.model ?? null;
      if (model !== null && !TRIAGE_MODELS.includes(model)) {
        return errorResponse(422, "validation_error", `model must be one of: ${TRIAGE_MODELS.join(", ")}`);
      }
      if (incident.status !== "open" && incident.status !== "awaiting_review") {
        return errorResponse(409, "invalid_state_transition", `triage_started is not allowed from ${incident.status}`);
      }
      incident.status = "triaging";
      mockDb.triage[incident.id] = { doneAt: Date.now() + MOCK_TRIAGE_MS, model: model ?? TRIAGE_MODELS[0]! };
      return HttpResponse.json<Incident>(incident, { status: 202 });
    },
  ),

  // Approve and Reject follow the backend (#63): only set_memory_limit can be applied, anything
  // else answers 501; the apply loop then runs the fix (settleApply).
  http.post<{ id: string }, { note?: string | null } | null, Body<Incident>>(
    `${API}/incidents/:id/remediation/approve`,
    async ({ params, request }) => {
      const denied = adminCheck();
      if (denied) return denied;
      const incident = findIncident(params.id);
      if (!incident) return notFound(`incident ${params.id}`);
      const remediation = incident.remediation;
      if (!remediation || remediation.status !== "proposed") return noProposal(incident.id);
      if (remediation.kind !== "set_memory_limit") {
        return errorResponse(
          501,
          "not_implemented",
          `approving a ${remediation.kind} remediation is not available yet; only set_memory_limit can be applied`,
        );
      }
      if (incident.status !== "awaiting_review") {
        return errorResponse(409, "invalid_state_transition", `remediation_approved is not allowed from ${incident.status}`);
      }
      const note = (await request.json().catch(() => null))?.note?.trim();
      const actor = mockSession.user!.username;
      const now = new Date().toISOString();
      incident.status = "remediating";
      Object.assign(remediation, {
        status: "approved",
        mode: "manual",
        decided_by: actor,
        decided_at: now,
        steps: [...remediation.steps, { step: "approved", at: now, ok: true, detail: `approved by ${actor}${note ? `: ${note}` : ""}` }],
      } satisfies Partial<Remediation>);
      mockDb.apply[incident.id] = Date.now() + MOCK_APPLY_MS;
      return HttpResponse.json<Incident>(incident);
    },
  ),

  http.post<{ id: string }, { reason?: string } | null, Body<Incident>>(
    `${API}/incidents/:id/remediation/reject`,
    async ({ params, request }) => {
      const denied = adminCheck();
      if (denied) return denied;
      const body = await request.json().catch(() => null);
      const reason = body?.reason?.trim();
      if (!reason) return validationError("reason", "Field required", "body");
      const incident = findIncident(params.id);
      if (!incident) return notFound(`incident ${params.id}`);
      const remediation = incident.remediation;
      if (!remediation || remediation.status !== "proposed") return noProposal(incident.id);
      const actor = mockSession.user!.username;
      const now = new Date().toISOString();
      incident.status = "closed";
      Object.assign(remediation, {
        status: "rejected",
        mode: remediation.mode ?? "manual",
        decided_by: actor,
        decided_at: now,
        steps: [...remediation.steps, { step: "rejected", at: now, ok: true, detail: `rejected by ${actor}: ${reason}` }],
      } satisfies Partial<Remediation>);
      return HttpResponse.json<Incident>(incident);
    },
  ),

  http.get<{ subject: string; id: string }, never, Body<EvidenceList>>(
    `${API}/:subject/:id/evidence`,
    ({ params }) => {
      if (!mockSession.user) return unauthorized();
      if (params.subject === "incidents") {
        if (!findIncident(params.id)) return notFound(`incident ${params.id}`);
        return HttpResponse.json<EvidenceList>({ items: mockDb.evidence[Number(params.id)] ?? [] });
      }
      if (params.subject === "changes") {
        if (!mockDb.changes.some((c) => String(c.id) === params.id)) return notFound(`change ${params.id}`);
        return HttpResponse.json<EvidenceList>({ items: [] });
      }
      return validationError("subject", "Input should be 'changes' or 'incidents'");
    },
  ),

  http.get<PathParams, never, Body<IntakeHealth>>(`${API}/intake/health`, () =>
    mockSession.user ? HttpResponse.json<IntakeHealth>(mockDb.intake) : unauthorized(),
  ),
];
