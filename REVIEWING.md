# Reviewing ConsoleTrack for the AI Dev Tools Zoomcamp

ConsoleTrack is a final project for the DataTalksClub
[AI Dev Tools Zoomcamp](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp) course. This guide is
for peers grading it against the
[course rubric](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/project/README.md)
(12 criteria, 24 points, 10 to pass). Each criterion below has the evidence and the shortest check.
You don't need AWS for any of it.

## Start here (2 min)

ConsoleTrack records the AWS changes made outside the pipeline (console, CLI, CloudShell, a local
`terraform apply`) and links later EKS incidents back to the change that likely caused them. An
on-call triage agent diagnoses the incident through read-only tools; one narrow fix, raising a
memory limit after an OOM kill, is applied automatically, verified, and rolled back if it fails.

**Who does what.** There is one AI agent, the triage agent (Claude), run by the worker. The two MCP
servers (`k8s-mcp`, `aws-mcp`) are its read-only toolsets, not agents. Fixes are applied by the
backend's own `remediator` identity, and only after the policy gate allows them.

**Four ways to see it**

| Way | Needs | Time |
|---|---|---|
| [Demo site](https://consoletrack-demo.jennasrunbooks.com): the real frontend, recorded sample data | a browser | 5 min |
| Walkthrough video of the live EKS deployment: <!-- TODO: YouTube link once recorded --> | a browser | 5 min |
| `make local`: the full system on kind and Docker Compose, no AWS ([README](README.md#try-it-without-aws)) | Docker, kind, an Anthropic API key | 30 min |
| `make up`: your own AWS account ([README](README.md#deploy-to-aws)) | AWS account, a domain | 45 min, about $0.20/hour |

**What is in the submission, and what is Phase 2.** The submitted state is the commit SHA given in the course submission.
The full table is in [product-spec.md → Scope](product-spec.md#scope).

| In the submission (Phase 1, Oct 26) | Phase 2 (Nov 16) |
|---|---|
| Alertmanager → incident → triage agent with read-only `k8s-mcp` | Revert (the gated GitHub workflow) |
| CloudTrail → EventBridge → SQS change intake, intake health, canary | Codify (the agent drafts a Terraform PR) |
| Read-only `aws-mcp` | Scripted scenarios `make demo-drift`, `make demo-identity` |
| Security-group scenario (by hand in the console) and three cluster faults | Eval harness `make eval` (every scenario, N runs) |
| Dashboard: incidents, change log, evidence, Accept, postmortem editor | Attribute-level drift from `terraform plan -refresh-only` |
| Automatic OOM fix behind the policy gate | More remediation kinds (rollback, probe fixes) |
| OpenTelemetry in Grafana (collector, Tempo, Loki, the Incident journey dashboard) on EKS and with `make local`; metrics reach Prometheus | A link from each incident and change to its Grafana trace |
| Terraform + Helmfile on EKS, `make local`, CI/CD, agent team and extension pack | `make demo-replay` (still a stub); dev auto-deploy with production behind approval |

## Three review paths

| Path | Time | Requirements | Do |
|---|---|---|---|
| No install | about 10 min | a browser | Demo site tour (below), the video, then read the evidence links per criterion |
| Local | about 30 min | Docker (4 CPUs, 8 GB), kind, kubectl, Helm 4, Helmfile, `make`, an Anthropic API key | `make local`, `make test-all`, one `make local-oom-run` |
| AWS | about 45 min, plus cost | your own AWS account, a domain | `make up` ... `make down`, then `make verify-clean` |

**No-install tour of the demo site** (a reload resets it):

1. Open the [demo site](https://consoletrack-demo.jennasrunbooks.com). No login. The "Take the 2-minute tour" banner lists the three stops below.
2. **INC-11**: an out-of-memory incident the agent diagnosed and the policy gate fixed automatically. Read the evidence and the verified fix.
3. **INC-14**: a real recorded run where an admin approves the fix by hand. Approve it, then try **Edit postmortem**.
4. **A console change waiting for review**: SSH opened to the internet. See who made it and what it touched.
5. Screenshots of all of this, light and dark, are in [docs/screenshots/](docs/screenshots/).

**Local path in detail.** Setup is in [README → Try it without AWS](README.md#try-it-without-aws).

```bash
cp .env.example .env && export ANTHROPIC_API_KEY=...
make local            # kind + Prometheus + demo app, then Compose; prints the admin password
make test-all         # unit, contract, frontend, and integration (needs Docker)
make local-oom-run    # one OOM run end to end (about 15 min); prints PASS or FAIL
make local-replay-fixtures   # optional: load the recorded CloudTrail changes
```

The change log starts empty until you run `make local-replay-fixtures`; the worker records the
fixtures and the agent analyzes them.

**AWS path in detail.** [README → Deploy to AWS](README.md#deploy-to-aws) lists the prerequisites and
steps, and [the cost table](README.md#cost) shows about $0.20 per hour while the cluster is up.
`make down` destroys it; `make verify-clean` lists any leftover load balancers, ENIs, volumes, Elastic
IPs, NAT gateways, and DNS records and fails if one remains.

## Criterion by criterion

Points are the rubric's maximum. Where something is partial, it says so.

### 1. Problem description (2)

- **Asks:** the problem, what the system does, and the expected behavior.
- **Evidence:** the opening of the [README](README.md); [product-spec.md → Summary](product-spec.md#summary), including "Expected behavior in the demo".
- **Verify:** read the first two README paragraphs (a minute).

### 2. Product specification (2)

- **Asks:** a written spec, plus a public task tracker with acceptance criteria that agents used.
- **Evidence:**
  - [product-spec.md](product-spec.md).
  - The [issue tracker](https://github.com/jennasrunbooks/consoletrack/issues?q=is%3Aissue).
  - Example: [issue #34](https://github.com/jennasrunbooks/consoletrack/issues/34) (OOM auto-fix, with scenario, scope, and acceptance criteria) → [PR #63](https://github.com/jennasrunbooks/consoletrack/pull/63), which says `Closes #34`.
  - Example: [issue #13](https://github.com/jennasrunbooks/consoletrack/issues/13) (frontend) → PRs [#62](https://github.com/jennasrunbooks/consoletrack/pull/62), [#75](https://github.com/jennasrunbooks/consoletrack/pull/75), [#76](https://github.com/jennasrunbooks/consoletrack/pull/76).
  - Example: [issue #12](https://github.com/jennasrunbooks/consoletrack/issues/12) (Helm, `make up`) → PRs [#50](https://github.com/jennasrunbooks/consoletrack/pull/50), [#77](https://github.com/jennasrunbooks/consoletrack/pull/77).
- **Verify:** open issue #34, then PR #63; check that the PR's "What changed" lines match the issue's scope list.

### 3. Agent team specification (2)

- **Asks:** the team, and how its members collaborate.
- **Evidence:** [docs/agent-team.md](docs/agent-team.md): [roles](docs/agent-team.md#roles), [how an issue becomes a merged PR](docs/agent-team.md#how-work-flows), [rules for parallel agents](docs/agent-team.md#rules-that-keep-parallel-agents-from-colliding), [access by role](docs/agent-team.md#access-by-role), [evidence](docs/agent-team.md#evidence).
- **Verify:** the [pull requests](https://github.com/jennasrunbooks/consoletrack/pulls?q=is%3Apr) authored by `jksprattler-agent` each close an issue, with the review thread on the PR.

### 4. Context management (2)

- **Asks:** an AGENTS.md or equivalent that links to modular docs.
- **Evidence:** [AGENTS.md](AGENTS.md) and its "Where to read more" table, pointing to [docs/agent-team.md](docs/agent-team.md), [docs/architecture.md](docs/architecture.md), [docs/permissions.md](docs/permissions.md), [docs/observability.md](docs/observability.md), [docs/remediation-graduation.md](docs/remediation-graduation.md), [docs/agent-extension-pack.md](docs/agent-extension-pack.md), and the per-area READMEs. It is the only instructions file, by design.
- **Verify:** open AGENTS.md; every row of the table is a link to a file that exists.

### 5. Application (2)

- **Asks:** frontend, backend, and database working end to end, with persistent data, running locally with one command.
- **Evidence:** [README → Try it without AWS](README.md#try-it-without-aws); [scripts/local-kind.sh](scripts/local-kind.sh); [docker-compose.yaml](docker-compose.yaml); [openapi.yaml](openapi.yaml) (the contract between frontend and backend); Postgres with Alembic migrations in `backend/app/db/`.
- **Verify:** `make local`, open http://localhost:8080, log in as `admin` with the printed password. No install: the [demo site](https://consoletrack-demo.jennasrunbooks.com) is the same frontend on sample data. The data lives in a Postgres volume until `make local-down`.

### 6. Testing (2)

- **Asks:** unit and integration tests, runnable with one command, passing.
- **Evidence:** [README → Testing and CI/CD](README.md#testing-and-cicd); [Numbers](#numbers-measured-not-guessed) below; the `test` and `integration` jobs in [.github/workflows/ci.yaml](.github/workflows/ci.yaml).
- **Verify:** `make test` (no Docker, no API key) prints green for every layer. `make test-all` adds the integration tests on Testcontainers Postgres and needs Docker. The latest CI run on `main` shows both.

### 7. Containerization (2)

- **Asks:** the whole system, including the database, starts with one command.
- **Evidence:** [docker-compose.yaml](docker-compose.yaml) (Postgres, ElasticMQ, backend, dashboard, `k8s-mcp`, worker, and the observability stack); [compose/](compose/); the Dockerfiles under `backend/`, `frontend/`, `demo-app/`, `mcp-server/`; `make test-compose` checks the Compose invariants (loopback-only ports, no literal secrets, pinned images) without Docker.
- **Verify:** `make local`; `docker compose ps` shows every service healthy. The Compose-only single command is `LOCAL_KIND=false make local`. It is a `make` target rather than a bare `docker compose up` so the admin password hash and generated secrets reach Compose correctly ([README → Try it without AWS](README.md#try-it-without-aws)). Images are arm64 for EKS and built for your machine locally.

### 8. Deployment (2)

- **Asks:** a public URL, or a clear demonstration of the deployment.
- **Evidence:**
  - EKS via [terraform/](terraform/) and [helm/](helm/); [README → Deploy to AWS](README.md#deploy-to-aws).
  - The always-on [demo site](https://consoletrack-demo.jennasrunbooks.com), published by the Pages workflow ([#89](https://github.com/jennasrunbooks/consoletrack/issues/89)).
  - The EKS screenshot of a real console change: [docs/screenshots/eks/](docs/screenshots/eks/change-detail-console-sg.png).
  - The walkthrough video of the live deployment: <!-- TODO: YouTube link once recorded -->.
- **Verify:** video timestamps: `make up` <!-- TODO mm:ss -->, the console change appearing as a change record <!-- TODO mm:ss -->. No cluster is kept running for visitors, so the live EKS deployment is shown by the video and screenshot. `make up` builds the same thing in your account.

### 9. CI/CD (3)

- **Asks:** CI on every PR (1), plus automatic deploy (2), plus deploy to dev with a manual production deploy (3). ConsoleTrack claims 2 of 3: it has one environment, deployed automatically. Dev auto-deploy with production behind approval is Phase 2.
- **Evidence:** [ci.yaml](.github/workflows/ci.yaml) (every PR and push, nightly; required checks `lint`, `test`, `terraform-fmt`); [deploy.yaml](.github/workflows/deploy.yaml) (green `main` → arm64 images to GHCR → `helmfile -l tier=app apply`); [terraform.yaml](.github/workflows/terraform.yaml) (plan on PRs, apply after approval); [README → Testing and CI/CD](README.md#testing-and-cicd). AWS access is GitHub OIDC; the deploy role only runs `-l tier=app`.
- **Rollback paths:** a failed Helm deploy rolls itself back (releases are `atomic: true` in [helm/helmfile.yaml.gotmpl](helm/helmfile.yaml.gotmpl)), and a failed automatic fix is rolled back by the backend ([docs/remediation-graduation.md](docs/remediation-graduation.md)).
- **Verify:** open any merged PR's checks; open the Actions tab for the `deploy` run after a green `ci` on `main`.

### 10. Observability (2)

- **Asks:** a key user journey with metrics, traces, and structured logs sharing a trace ID, no customer data or credentials in telemetry, viewable in a UI.
- **Evidence:**
  - [docs/observability.md](docs/observability.md): [the journey](docs/observability.md#the-journey), [metrics](docs/observability.md#metrics), [logs](docs/observability.md#logs), [privacy](docs/observability.md#privacy).
  - The redaction test: [backend/tests/unit/test_telemetry_redaction.py](backend/tests/unit/test_telemetry_redaction.py) (bearer tokens, cookies, IPs, ARNs, account IDs, and keys in the inputs must not appear in exported telemetry).
  - Screenshots (local and EKS): [docs/observability.md → Screenshots](docs/observability.md#screenshots).
- **Verify:** after `make local` (or on EKS) and a fault run, open Grafana at http://localhost:3000, the **Incident journey** dashboard, and follow one trace from `alert.receive` to `incident.resolve`, then jump to its logs. `cd backend && uv run pytest tests/unit/test_telemetry_redaction.py` passes.
- **Both environments** (EKS and `make local`) have the OpenTelemetry collector, Tempo, Loki, and the **Incident journey** dashboard. Metrics reach Prometheus by remote write from the collector. EKS screenshots: [docs/observability.md → Screenshots](docs/observability.md#screenshots).

### 11. Alerting on user impact (1)

- **Asks:** at least one alert.
- **Evidence:** `DemoAppHighErrorRate` in [helm/charts/demo-app/templates/prometheusrule.yaml](helm/charts/demo-app/templates/prometheusrule.yaml) (line 20): more than 5% of the traffic generator's requests fail over 2 minutes, for 1 minute. It's measured on the client's side, so it fires even when the app is down. Why: [docs/observability.md → The user-impact alert](docs/observability.md#the-user-impact-alert).
- **Verify:** `make local-fault FAULT=oom`; about 4 minutes later http://localhost:9090/alerts shows it firing (port-forward steps in [the README](README.md#try-it-without-aws)) and an incident appears.

### 12. On-call agent (2)

- **Asks:** an agent wakes on an alert and fixes the issue.
- **Evidence:**
  - [docs/remediation-graduation.md](docs/remediation-graduation.md): [criteria](docs/remediation-graduation.md#graduation-criteria), [gate](docs/remediation-graduation.md#the-gates-checks), and [eval results](docs/remediation-graduation.md#eval-results-and-the-confidence-floor).
  - Eval: 5/5 on Haiku (the default `TRIAGE_MODEL`) and 5/5 on Sonnet, both at `minConfidence: medium`; the proposal was correct in 20 of 20 runs.
  - Code: [policy_gate.py](backend/app/services/policy_gate.py), [remediation_validator.py](backend/app/services/remediation_validator.py).
  - Spec: [product-spec.md → Remediation modes](product-spec.md#remediation-modes). Built in [issue #34](https://github.com/jennasrunbooks/consoletrack/issues/34) / [PR #63](https://github.com/jennasrunbooks/consoletrack/pull/63).
- **Verify:** demo site, incident **INC-11**: alert, diagnosis, automatic fix, resolved. Locally, `make local-oom-run` prints PASS after the incident resolves. Video: <!-- TODO mm:ss -->.
- **Partial:** the gate's `medium` threshold relies on self-reported confidence; the reasons and the planned evidence-based gate are in the graduation doc. Rollback of a bad image stays manual, and Revert is Phase 2.

## Numbers, measured not guessed

Run in the devcontainer on Oct 24, from the repo root. These need no Docker and no API key.

| Layer | Command | Result |
|---|---|---|
| Backend unit | `make test-backend` | 649 passed, `app/services/` coverage 98% |
| Contract | `make test-contract` | 83 passed |
| k8s-mcp | `make test-k8s-mcp` | 133 passed, coverage 93% |
| aws-mcp | `make test-aws-mcp` | 132 passed, coverage 97% |
| demo-app | `make test-demo-app` | 38 passed |
| Helm (rendered manifests) | `make helm-lint` | 164 passed |
| Frontend | `make test-frontend` | 125 Vitest tests passed; 67 Playwright e2e tests passed |
| Agent hooks | `make test-hooks` | 286 passed |
| Terraform (event pattern) | `make test-terraform` | 20 passed |
| Compose invariants | `make test-compose` | 50 passed |

**Not run here (need Docker, so they run in CI):** the backend integration tests (`make test-integration`,
Testcontainers Postgres), the kind RBAC tests (`make test-k8s`), `make local`, and `make demo-replay`.

## Safety and permissions

"Can the agent break things?" The short answer is no, for several independent reasons.

- **Triage agent:** read-only. It reaches the cluster and AWS only through `k8s-mcp` and `aws-mcp`, and every tool is a read, list, or describe call ([docs/permissions.md → Runtime triage agent](docs/permissions.md#runtime-triage-agent), [k8s-mcp checks](docs/permissions.md#k8s-mcp-checks-inside-the-server), [aws-mcp](docs/permissions.md#aws-mcp-tools-apis-and-iam)).
- **Policy gate:** the agent only proposes. Only `set_memory_limit` in the `demo` namespace can be `auto`, bounded to 2× and 512Mi, with a kill switch, one auto fix per workload per hour, and automatic rollback ([docs/remediation-graduation.md](docs/remediation-graduation.md)). AWS-side kinds can't be set to `auto`; the backend refuses to start.
- **Coding agents:** a PreToolUse hook, [agent-hooks/guard.py](agent-hooks/guard.py), blocks `terraform apply`, mutating `kubectl`/`helm`, force pushes, and reads of `.env`, wired in [.claude/settings.json](.claude/settings.json) and described in [docs/agent-extension-pack.md](docs/agent-extension-pack.md#hooks). The agents hold no AWS or cluster credentials, can't edit workflows, and can't merge ([AGENTS.md → Hard rules](AGENTS.md#hard-rules)).
- **Gaps we know about:** [docs/permissions.md → Accepted gaps](docs/permissions.md#accepted-gaps).
