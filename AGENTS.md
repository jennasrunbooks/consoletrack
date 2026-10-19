# AGENTS.md — ConsoleTrack

Guidelines for AI coding agents (Claude Code as `jksprattler-agent`) and humans working in this repo.
`product-spec.md` is the source of truth for *what* to build; this file is *how* to work here.
If the two disagree, stop and ask — don't pick one silently.

## What ConsoleTrack is

ConsoleTrack turns undocumented cloud changes into documented, reviewable records. It watches
CloudTrail (→ EventBridge → SQS) for out-of-band AWS changes — console, CLI, CloudShell, SDK, or a
`terraform apply` run outside the pipeline — stores each as a change record, and links later EKS
incidents (e.g. `AccessDenied`) back to the change that likely caused them. Reviewers **Revert**
(gated GitHub workflow re-applies Terraform), **Codify** (Phase 2 PR), or **Accept** with a note.
The triage agent only reads.

## Where to read more

This file is the entry point. Read the doc for the area you're working in before starting an issue.

| Doc | Read it when |
|---|---|
| [product-spec.md](product-spec.md) | Always: the section your issue names. What to build and why |
| [docs/agent-team.md](docs/agent-team.md) | Before your first issue: roles, how an issue becomes a merged PR, the rules for parallel agents |
| [docs/architecture.md](docs/architecture.md) | Backend work: layers, state machines, database |
| [docs/permissions.md](docs/permissions.md) | Anything touching IAM, RBAC, tokens, or guardrails; includes the accepted gaps |
| [docs/agent-extension-pack.md](docs/agent-extension-pack.md) | Changing hooks, skills, the subagent, or MCP wiring |
| [docs/observability.md](docs/observability.md) | Adding a span, metric, log field, or alert; the privacy allowlist |
| [docs/remediation-graduation.md](docs/remediation-graduation.md) | Changing the policy gate or adding a remediation kind |
| [openapi.yaml](openapi.yaml) | Any API change: update it first |
| [terraform/consoletrack/global/README.md](terraform/consoletrack/global/README.md), [terraform/consoletrack/us-east-1/README.md](terraform/consoletrack/us-east-1/README.md) | Terraform work: bootstrap order, roles, naming, the intake pattern |
| [helm/README.md](helm/README.md) | Helmfile releases, environments, values |
| [backend/README.md](backend/README.md) | Running and testing the backend |
| [docs/proposed-workflows/](docs/proposed-workflows/) | You need a workflow change (you can't edit `.github/workflows/`) |


## Stack

| Area | Stack | Location |
|---|---|---|
| Backend + worker | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, managed with `uv` | `backend/` |
| Triage agent | Anthropic Python SDK, structured output via the `submit_analysis` tool | `backend/app/agents/` |
| MCP servers | FastMCP (streamable HTTP), read-only | `mcp-server/k8s/`, `mcp-server/aws/` |
| Frontend | React + Vite + TypeScript | `frontend/` |
| Database | Postgres (CloudNativePG in cluster, Compose/Testcontainers locally) | — |
| Infra | Terraform ≥ 1.11, terraform-aws-modules, EKS (AWS only) | `terraform/` |
| Cluster releases | Helmfile + Helm 4 (with helm-diff); every chart, ours and third-party | `helm/` |
| CI/CD | GitHub Actions with OIDC to AWS | `.github/workflows/` (human-edited only, see below) |

Images are built for **arm64** (Graviton nodes). amd64 is only for local Compose.

## Repository map

```text
product-spec.md            # the spec — read the relevant section before starting an issue
openapi.yaml               # API contract, source of truth for backend ↔ frontend
docker-compose.yaml  .env.example  Makefile
backend/app/
  api/                     # routers: HTTP only, call services, never touch the DB session
  services/                # change + incident state machines, remediation validator, policy gate
  intake/                  # SQS consumer, CloudTrail event parsing, canary heartbeat
  agents/                  # agent loop, prompts/, submit_analysis schema
  db/                      # SQLAlchemy models, Alembic migrations
backend/tests/{unit,integration,contract}/
mcp-server/k8s/            # k8s-mcp
mcp-server/aws/            # aws-mcp (+ fixtures mode for demo-replay)
frontend/src/api/          # the ONLY place the frontend calls the backend
terraform/<account>/<global|region>/   # consoletrack/{global,us-east-1}, demo-app/us-east-1
helm/helmfile.yaml.gotmpl  # every release (tier=addons|app), pinned chart versions, environments
helm/charts/{consoletrack,demo-app,gateway-api-crds,cluster-defaults}/  helm/values/  helm/environments/  helm/tests/
scripts/  scripts/scenarios/
agent-capabilities/  agent-hooks/  custom-agent/  docs/
```

## Commands

Run from the repo root unless noted. Prefer `make` targets over ad-hoc commands.

| Task | Command |
|---|---|
| Unit tests (fast, run before every commit) | `make test` |
| Lint + format check | `make lint` |
| Backend tests directly | `cd backend && uv run pytest -m "not integration"` |
| Integration tests (needs Docker) | `cd backend && uv run pytest -m integration` |
| Frontend | `cd frontend && npm ci && npm run lint && npm test` |
| Helm checks (no cluster calls) | `make helm-lint` (`helmfile lint` + `template` for eks, eks with alb, and local, then the rendered-manifest checks; part of `make lint`). Without Helmfile, `make helm-test` runs the checks on `helm template` output |
| Terraform checks (no AWS calls) | `terraform fmt -check -recursive terraform/` and `terraform -chdir=terraform/<account>/<root> validate` |
| Local stack without AWS | `make demo-replay` |

If a `make` target doesn't exist yet, add it in the same PR instead of documenting a one-off command.

Integration tests, `make local`, and `make demo-replay` need Docker, which the devcontainer deliberately doesn't have, so they run in CI (and on the host); don't try to install Docker.

## Hard rules

These are enforced by `.claude/settings.json`, hooks in `agent-hooks/`, the agent's GitHub token,
and the `main` ruleset. Don't try to work around a block — report it.

1. **Never change real infrastructure.** No `terraform apply`/`destroy`/`import`/`state` writes, no
   `kubectl apply|create|delete|edit|patch|scale`, no `helm install|upgrade|uninstall`, no
   `helmfile apply|sync|destroy|delete|init`, no AWS CLI or SDK calls that write. Infra changes
   ship as code in a PR; CI posts the plan and a human applies.
2. **You have no AWS or cluster credentials, and that is intended.** Don't ask for them, don't look
   for them in `~/.aws`, `~/.kube`, or environment variables.
3. **Never read or print `.env` or `.env.*`** (`.env.example` is fine). Never put secrets, API keys,
   tokens, or **AWS account IDs** in code, tests, fixtures, logs, or PR text. Use `123456789012` as
   the placeholder account ID in fixtures; Terraform reads real IDs from `data.aws_caller_identity`.
4. **Don't edit `.github/workflows/`.** The agent token has no Workflows permission, so pushes that
   touch it are rejected. Put the proposed file in `docs/proposed-workflows/<name>.yaml` and say in
   the PR that a human needs to move it.
5. **Never commit to `main`.** Work on a branch, open a PR, and wait for review. Never merge your own
   PR, change rulesets, environments, or repo settings.
6. **The MCP servers stay read-only.** Any new `k8s-mcp` or `aws-mcp` tool must be a read/list/describe
   call, must be added to the tool table in `product-spec.md`, and must come with the matching
   least-privilege IAM or RBAC change in the same PR.
7. **The triage agent never applies anything.** Cluster remediation goes through the policy gate
   (manual by default); AWS-side changes always go through Revert or Codify with human approval.
   Don't add code paths that bypass the gate.
8. **Don't download and run binaries or installers from the internet.** Install tools only through
   the project's package managers (uv, npm) or the devcontainer. If a task needs a tool the
   container doesn't have, stop and ask.

## Conventions

**Naming (AWS)**
- ConsoleTrack-owned resources: `ct-<component>-<suffix>` for global ones (IAM, the state bucket,
  the budget) and `ct-<component>-<region>-<suffix>` for regional ones, where `<region>` is the
  short code from `local.region_codes` (`use1` for us-east-1), e.g. `ct-eks-use1-a1b2c3`. IAM path
  `/ct/`, and every role gets the `ct` permissions boundary.
- Demo app resources: `demo-app-<component>`, IAM path `/demo-app/`.
- Every resource is tagged `project = consoletrack` (set via provider `default_tags`).
- The repo, Helm chart, namespace, and images stay `consoletrack` (`ghcr.io/jennasrunbooks/consoletrack-*`).

**Terraform**
- Use public, supported modules (terraform-aws-modules first) with pinned versions; write raw
  resources only when no module fits, and say why in the PR.
- One root per `terraform/<account>/<global|region>/`. No cross-root `terraform_remote_state`
  unless the spec calls for it.
- Configuration comes from `.env` → `TF_VAR_*` via the Makefile. No hardcoded regions, account IDs,
  or CIDRs; add a variable and an `.env.example` entry instead.
- IAM: least privilege, scoped to resource ARNs where the service allows it, no `*:*`. Explain any
  wildcard in a comment.
- Commit `.terraform.lock.hcl`.

**Helm**
- Terraform is AWS-only; Helmfile owns every chart. Every release, third-party and ours, lives in
  `helm/helmfile.yaml.gotmpl` with a `tier=addons` or `tier=app` label. Environments are deployment
  targets (`eks` default, `local` for kind), not apps.
- Chart versions are pinned. Values come from `helm/values/` (third-party), the chart's own
  `values.yaml` (ours: `helm/charts/<chart>/values.yaml`), and the generated environment file
  (`helm/environments/values.generated.yaml`, from `.env`, gitignored).
- **Never** add the Terraform `helm` or `kubernetes` provider. The guard rejects it in `*.tf`
  edits and `make lint` (`lint-terraform-providers`) fails on it.
- `make addons` (`helmfile -l tier=addons apply`) and `make deploy` (`helmfile -l tier=app apply`)
  are for humans and CI; the CI deploy role only ever runs `-l tier=app`.

**Backend**
- OpenAPI first: change `openapi.yaml` before (or in the same PR as) any route, schema, or enum change.
- Routers call services; only `app/db/` and services touch the session. Every mutating endpoint
  validates input with a Pydantic model.
- State changes for changes/incidents go through the state machine in `app/services/` — never set
  `status` directly.
- Database changes ship with an Alembic migration.

**Frontend**
- Only `frontend/src/api/` calls the backend. Keep types in sync with `openapi.yaml`.
- Match the mockups in `docs/mockups/` (light and dark theme, yellow prompt-caret logo).

**YAML and files**
- Use `.yaml`, not `.yml`.

## Git workflow

1. One issue per branch and PR. Start from an up-to-date `main`.
2. Branch names: `feat/<area>-<short-desc>`, `fix/<area>-<short-desc>`, `chore/<area>-<short-desc>`,
   `docs/<short-desc>`. Areas: `backend`, `intake`, `agent`, `mcp`, `frontend`, `terraform`, `helm`,
   `ci`, `docs`.
3. Conventional Commits with the area as scope, e.g. `feat(intake): parse ConsoleLogin and AssumeRole events`.
4. Before every commit: `make lint` and `make test` pass. Don't commit failing tests or skip them
   to get green.
5. Push the branch and open a PR that:
   - says `Closes #<issue>`;
   - summarizes the change and links the spec section it implements;
   - lists how it was tested (commands + results);
   - for Terraform: lists the resources you expect the plan to add/change/destroy, so the reviewer
     can compare against CI's plan;
   - calls out anything security-relevant (IAM, RBAC, network, secrets handling).
6. Squash merge is the default; a human merges.

## When to stop and ask

- The spec is ambiguous, contradicts itself, or contradicts this file.
- A task needs credentials, a workflow change, a new AWS service, or wider IAM/RBAC than the spec lists.
- A change would add cost (new always-on AWS resource, larger instances, NAT gateway, load balancer).
- You've hit the same failure twice. Explain what you tried instead of trying a third variation.

## Agent extension pack

The course's agent extension pack lives in `agent-capabilities/`, `agent-hooks/`, `mcp-server/`, and
`custom-agent/`, documented in `docs/agent-extension-pack.md` and `docs/permissions.md`.
`.claude/settings.json` wires in the hooks (a PreToolUse guard for the hard rules above, and a
PostToolUse lint on edited files). `.claude/settings.local.json` is personal and gitignored.

This file is the only project instructions file. Don't add a `CLAUDE.md`, `.claude/CLAUDE.md`, or
`CLAUDE.local.md` anywhere in the repo: Claude Code loads `AGENTS.md` only when none of those exist.
