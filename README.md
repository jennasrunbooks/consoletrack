# ConsoleTrack

<!-- TODO badges: CI | License -->

Someone opens the AWS console (or the CLI, CloudShell, an SDK script, or a local `terraform apply`), changes a security group, and moves on. Nobody records why. A week later a pod gets `AccessDenied` and the on-call engineer starts from scratch.

ConsoleTrack catches every out-of-band AWS change within minutes, records who made it, how, and to which Terraform-managed resource, and links later EKS incidents back to the change that most likely caused them. A reviewer can **Revert** it (a gated GitHub workflow re-applies the reviewed Terraform), **Codify** it (open a PR adopting it, Phase 2), or **Accept** it with a note. Either way the change ends up documented. The triage agent only reads; every fix needs a human's approval.

![ConsoleTrack change log](docs/mockups/change-log.png)

<sub>Design mockup; replaced with screenshots of the running app before submission.</sub>

## Contents

- [How it works](#how-it-works)
- [Try it without AWS](#try-it-without-aws)
- [Deploy to AWS](#deploy-to-aws)
- [Cost](#cost)
- [Configuration](#configuration)
- [Using ConsoleTrack](#using-consoletrack)
- [Triage agent and MCP servers](#triage-agent-and-mcp-servers)
- [Agent extension pack](#agent-extension-pack)
- [Security and permissions](#security-and-permissions)
- [Development](#development)
- [Testing and CI/CD](#testing-and-cicd)
- [Repository layout](#repository-layout)
- [Roadmap](#roadmap)
- [Course rubric mapping](#course-rubric-mapping)
- [License](#license)

## How it works

```mermaid
flowchart TB
  browser[Browser] --> gw["Gateway<br/>port-forward or ALB"]
  claude["Claude API"]
  github["GitHub<br/>gated revert job"]
  subgraph eks["EKS cluster"]
    fe["Frontend<br/>React SPA"]
    be["Backend API<br/>approvals, apply"]
    worker["Triage worker<br/>agent + SQS consumer"]
    k8smcp["k8s-mcp<br/>read-only RBAC"]
    awsmcp["aws-mcp<br/>read-only role"]
    prom["Prometheus<br/>Alertmanager"]
    pg[("Postgres<br/>CNPG")]
    demo["Demo app<br/>reads its S3 bucket"]
    k8sapi["Kubernetes API<br/>EKS control plane"]
  end
  subgraph aws["AWS account · us-east-1"]
    res["App resources<br/>edited in console"] --> trail["CloudTrail<br/>one trail"] --> eb["EventBridge rule<br/>non-Terraform writes"] --> sqs["SQS queue<br/>change events"]
  end
  gw -- UI --> fe
  gw -- /api --> be
  be -- SQL --> pg
  be == "cluster patch (approved)" ==> k8sapi
  be -- dispatch revert --> github
  github == "terraform apply (approved)" ==> res
  prom -- alert webhook --> be
  prom -- scrapes --> demo
  demo -- Pod Identity --> res
  sqs -- change events --> worker
  worker -- Messages API --> claude
  worker -- MCP --> k8smcp
  worker -- MCP --> awsmcp
  k8smcp -- read --> k8sapi
  awsmcp -- read-only APIs --> aws
```

The agent only reads; every fix goes through an approval (thick arrows).

1. **Intake.** A CloudTrail trail feeds an EventBridge rule that keeps write events from people and scripts and drops pipeline sessions (`gh-<run id>`), AWS services acting on your behalf, and ConsoleTrack's own controllers. Matches land in an SQS queue with a dead-letter queue.
2. **Change records.** The worker parses each event (who, `via` = console, CLI, CloudShell, SDK, or Terraform outside the pipeline, source IP, resource), dedupes it, looks up the resource's Terraform address, and stores a change record in Postgres.
3. **Analysis.** The triage agent (Claude) reads the change and related CloudTrail activity through the read-only `aws-mcp` server and writes a summary, a risk rating, and options.
4. **Incidents.** Alertmanager posts alerts to the backend, which opens an incident. The agent gathers pod evidence through the read-only `k8s-mcp` server; if logs show an AWS error such as `AccessDenied`, it links the incident to the recent change that caused it.
5. **Review.** Revert, Codify (Phase 2), or Accept for changes; approve or reject the proposed fix for incidents. AWS-side fixes always need a human.
6. **Intake health.** A canary CronJob makes a known, harmless change every 15 minutes, so the header indicator can tell "nothing changed" from "intake is broken": Live, Delayed, Down, or Replay.

Full design: [product-spec.md](product-spec.md) · API: [openapi.yaml](openapi.yaml)

## Try it without AWS

Everything except the live AWS intake and Revert runs on your laptop: the UI, API, database, agent loop, cluster faults on kind, and a replay of recorded CloudTrail events.

Prerequisites: Docker, [kind](https://kind.sigs.k8s.io/), `make`, and an Anthropic API key.

```bash
git clone https://github.com/jennasrunbooks/consoletrack.git
cd consoletrack
cp .env.example .env
export ANTHROPIC_API_KEY=...   # or keep it in a password manager: op run --env-file=.env -- make local
make local                     # Docker Compose + a kind cluster with the demo app
make demo-replay               # replays recorded CloudTrail events into the local queue
# open http://localhost:5173
```

The intake indicator shows **Replay**, and Revert and Codify are disabled with "AWS required". `make local-down` removes everything.

**Logging in.** There are two fixed accounts, `admin` (can act) and `viewer` (read-only). Run `make hash-password` once per account and paste each output into `.env` as `ADMIN_PASSWORD_HASH` and `VIEWER_PASSWORD_HASH`; it prints an argon2id hash with every `$` doubled, which `.env` needs because make reads it. An account with no hash can't log in. The backend also needs a `SESSION_SECRET` of at least 32 bytes and refuses to start without one; Compose generates it for local runs. Five failed logins for one username, or from one address, within five minutes lock further attempts for five minutes (`429`).

The session cookie is always `Secure`, with no setting to turn that off. Chrome and Firefox treat `http://localhost` as a secure context and keep the cookie over plain HTTP. Safari may not: if login seems to succeed but the next page says you're logged out, use Chrome or Firefox.

## Deploy to AWS

### Prerequisites

- An AWS account you can use as a sandbox, with admin access and an AWS CLI profile
- Terraform ≥ 1.11, AWS CLI v2, kubectl, Helm 3 with the helm-diff plugin, Helmfile, jq, `make`. Or open [.devcontainer/](.devcontainer/), which has them pinned.
- An Anthropic API key with a monthly spend limit
- Optional: a domain you control (`DOMAIN`, `DNS_PARENT`). Required only for `EXPOSURE=alb`, because the load balancer serves HTTPS only.

### Steps

```bash
cp .env.example .env           # fill in the values, see Configuration
make preflight                 # tools, AWS identity, Spot quota, Kubernetes version, API key, CAA
make bootstrap                 # once: state bucket; prints a NAME_SUFFIX to add to .env
make up                        # Terraform (global, us-east-1, demo-app), then Helmfile (add-ons, app)
make open                      # port-forwards the dashboard and prints the URL and admin password
```

`make up` takes about 20–25 minutes and is safe to rerun. Terraform builds only AWS resources; Helmfile installs everything inside the cluster.

### Optional: GitHub Actions on your fork

To plan and apply Terraform from pull requests and run Revert as a gated workflow, set `ENABLE_GITHUB_OIDC=true` and `GITHUB_REPO=<you>/consoletrack` before `make up`, then in your fork:

1. Create the environments `consoletrack-plan`, `consoletrack-apply`, `consoletrack-revert`, and `consoletrack-deploy`. Require your approval on `-apply` and `-revert`, and limit all but `-plan` to `main`.
2. Add an `AWS_ROLE_ARN` environment secret to each, from `terraform -chdir=terraform/consoletrack/global output -json`.

Without it, Revert still works: the dashboard shows `make revert CHANGE=<id>`, which plans and applies the demo-app root from your machine after a confirmation.

### Demo scenarios

```bash
make demo-drift                # opens port 22 to the internet on the demo security group (no outage)
make demo-identity             # removes s3:GetObject from the demo app's role → AccessDenied incident
make demo                      # the three cluster faults (OOM, bad image, bad probe), one at a time
```

Each one prints the dashboard link to the change or incident it creates.

### Teardown

```bash
make down                      # Helmfile destroy, then the demo-app and regional Terraform roots
make verify-clean              # fails if any project-tagged billable resource is left
make down-all                  # optional: also the global root and the state bucket
```

## Cost

About **$0.20 per hour** while the cluster is up, about $0.24 with the optional load balancer. Run `make down` when you're done; `global` (IAM roles, DNS zone) costs pennies a month and can stay.

| Item | $/hour |
|---|---|
| EKS control plane, standard support | 0.100 |
| 2× t4g.medium Spot nodes | 0.040 |
| One managed NAT gateway, plus its public IPv4 (and $0.045/GB processed) | 0.050 |
| EBS gp3, about 60 GB | 0.007 |
| **Total, default** | **about 0.20** |
| Optional ALB (`EXPOSURE=alb`) | +0.035 |

Claude usage is per triage run; the default model is Haiku-class. Details and sources: [product-spec.md → AWS design and cost](product-spec.md#aws-design-and-cost).

## Configuration

`.env` is the only config file; [.env.example](.env.example) documents every variable. The Makefile turns it into `TF_VAR_*` variables, the Terraform backend settings, and Helmfile's environment values. Anything set in your shell wins over `.env`, so secrets can stay out of the file. `make env-check-up` lists anything missing (names only, never values).

| Variable | Default | Description |
|---|---|---|
| `AWS_PROFILE` | | AWS CLI profile for your sandbox account |
| `AWS_REGION` | `us-east-1` | Keep it: IAM's CloudTrail events reach EventBridge only in us-east-1 |
| `NAME_SUFFIX` | generated | Suffix on every resource name: `ct-<component>-<suffix>` for global resources, `ct-<component>-<region>-<suffix>` for regional ones (`ct-eks-use1-<suffix>`), from `make bootstrap` |
| `GITHUB_REPO` | | `owner/repo` the GitHub OIDC roles trust (your fork) |
| `ENABLE_GITHUB_OIDC` | `false` | Create the GitHub OIDC provider and CI roles |
| `GITHUB_WEBHOOK_SECRET` | | Optional; only with `EXPOSURE=alb`. The backend polls Revert runs anyway |
| `K8S_VERSION` | `1.36` | EKS version, in standard support until Aug 2, 2027 |
| `USE_SPOT` | `true` | Spot nodes; set `false` if you hit capacity errors |
| `NODE_INSTANCE_TYPE` | `t4g.medium` | Graviton (arm64) nodes |
| `VPC_CIDR` | `10.0.0.0/16` | VPC range; change it only if it overlaps a network you connect to |
| `EKS_PUBLIC_ACCESS_CIDRS` | `0.0.0.0/0` | Who can reach the EKS API's public endpoint (IAM still required); keep it open while CI deploys from GitHub-hosted runners |
| `EXPOSURE` | `port-forward` | `port-forward` (nothing public) or `alb` (needs `DOMAIN`) |
| `ALLOWED_CIDRS` | your IP | Who can reach the ALB |
| `DOMAIN` | | HTTPS hostname, e.g. `consoletrack.example.com` |
| `DNS_PARENT` | `external` | `external` (add NS records at your DNS provider) or `route53` |
| `ENABLE_BUDGET` | `true` | Create a $`BUDGET_LIMIT_USD`/month AWS Budgets alert to `BUDGET_EMAIL` |
| `ANTHROPIC_API_KEY` | | Leave empty in `.env` and set it in your shell or password manager |
| `TRIAGE_MODEL` | Haiku-class | Default triage model; `TRIAGE_MODEL_ESCALATION` for re-runs |
| `WATCHED_IAM_PATHS` | `/demo-app/` | IAM paths the intake and `aws-mcp` treat as watched |
| `ADMIN_PASSWORD_HASH`, `VIEWER_PASSWORD_HASH` | | argon2id hashes from `make hash-password`; empty means that account can't log in |
| `SESSION_SECRET` | | Signs the session cookie, at least 32 bytes; generated by Compose locally and set by `scripts/create-secrets.sh` on EKS |

## Using ConsoleTrack

<!-- TODO: replace mockups with screenshots of the running app -->

| Change log | Change detail |
|---|---|
| ![Change log](docs/mockups/change-log.png) | ![Change detail](docs/mockups/change-details.png) |

- **Change log:** every out-of-band change, newest first, with status chips, filters (status, principal, service, risk, date), and `via` badges.
- **Change detail:** who, what, when, and how; the Terraform address; the agent's analysis and evidence; and the Revert, Codify, and Accept actions.
- **Incidents:** alert-driven incidents with linked changes, the proposed fix, and Approve or Reject.
- **Intake indicator:** click it for the health details, the four states, and a link to the Grafana dashboard.

Light and dark themes follow your OS setting; the header toggle remembers your choice. Logins are an **admin** (can act) and a read-only **viewer**.

## Triage agent and MCP servers

The agent calls Claude through the Anthropic SDK and can only use tools on two read-only MCP servers. It ends each run by calling `submit_analysis`, whose JSON schema is the structured result. Limits per run: 12 tool calls, 120 seconds, a token budget, and one retry.

| Server | Tools | Read-only because |
|---|---|---|
| `k8s-mcp` | `get_workload`, `get_pods`, `get_pod_logs`, `get_events`, `get_rollout_history`, `query_prometheus` | Its ServiceAccount has `get`/`list`/`watch` in allowlisted namespaces only; no Secrets or ConfigMaps |
| `aws-mcp` | `get_resource_changes`, `get_principal_activity`, `get_iam_role`, `simulate_access`, `get_security_group`, `lookup_terraform_address` | Its Pod Identity role allows reads only, and the server itself refuses anything outside the watched IAM paths and the demo-app state |

Pod logs, events, and CloudTrail fields are treated as untrusted input, and results are redacted before they're stored or sent to the model. Proposed fixes pass an allowlist validator and a policy gate that ships in `manual` mode for every scenario.

## Agent extension pack

How this repo was built with Claude Code, and the pieces that make that safe and repeatable. Overview: [docs/agent-extension-pack.md](docs/agent-extension-pack.md).

| Piece | Where |
|---|---|
| Project instructions | [AGENTS.md](AGENTS.md) (read by Claude Code directly; there's no `CLAUDE.md`) |
| Reusable workflows (skills) | [agent-capabilities/](agent-capabilities/) |
| Specialist subagent | [custom-agent/](custom-agent/) |
| MCP servers | [mcp-server/](mcp-server/) and `.mcp.json` |
| Hooks and guardrails | [agent-hooks/](agent-hooks/) (PreToolUse guard, PostToolUse lint) and [.claude/settings.json](.claude/settings.json) |
| Permission notes | [docs/permissions.md](docs/permissions.md) |

## Security and permissions

- **No long-lived AWS keys.** GitHub Actions assumes OIDC roles (plan, apply, revert, deploy), each trusting one GitHub environment; pods use EKS Pod Identity, one role per ServiceAccount.
- **Least privilege.** ConsoleTrack's IAM lives under `/ct/` with a permissions boundary; the revert role can change only the demo-app root. The node role has no CNI, EBS, or load balancer permissions.
- **Human approval for every AWS change.** Apply and Revert wait in the `consoletrack-apply` and `consoletrack-revert` environments.
- **Network.** Nodes are in private subnets with no public IPs. Nothing is public by default; the optional ALB is HTTPS-only and limited to `ALLOWED_CIDRS`. The EKS API endpoint is public and IAM-authenticated, so GitHub-hosted runners can deploy.
- **App.** Admin and viewer logins checked against argon2id hashes (never plaintext), with a signed 8-hour `HttpOnly`, `Secure`, `SameSite=Strict` session cookie and a limit of 5 failed logins per username and per address in 5 minutes. Every API route requires a session and every write requires admin, which a test checks against the route table. Secrets are written to Kubernetes Secrets by a script, never to Terraform state or Helm values.
- **No account IDs in the repo or logs.** CI masks the account ID, and test fixtures use `123456789012`.
- **The coding agent** has no AWS or cluster credentials, can't edit workflows, and is fenced by a guard hook. See [docs/permissions.md](docs/permissions.md).

## Development

Conventions for humans and coding agents: [AGENTS.md](AGENTS.md). The devcontainer has every tool pinned and deliberately no Docker, AWS, or cluster credentials.

```bash
make help                      # every target
make lint                      # ruff, OpenAPI validation, terraform fmt, helmfile lint
make test                      # unit and contract tests, no network or API key needed
make tf-validate               # terraform init -backend=false + validate for every root
```

## Testing and CI/CD

| Layer | Tooling | Command |
|---|---|---|
| Backend unit | pytest, fake repositories, fake model | `make test-backend` |
| Contract | FastAPI schema vs `openapi.yaml` | `make test-contract` |
| Frontend | Vitest, React Testing Library, MSW | `make test-frontend` |
| Integration | pytest + Testcontainers Postgres + ElasticMQ | `make test-integration` |
| Cluster | `k8s-mcp` against kind with the real RBAC | `make test-k8s` |
| Agent eval (manual) | fault scenarios with the real model | `make eval` |

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yaml` | every PR and push | `lint`, `test`, and `terraform-fmt` jobs (required checks) |
| `terraform.yaml` | PRs and merges touching `terraform/**` | per-account matrix: plan on PRs, apply on `main` after approval in `<account>-apply` |
| `deploy.yaml` | green `main` | pushes arm64 images to GHCR, then `helmfile -l tier=app apply` if the cluster is up |
| `revert.yaml` | the Revert button | re-applies `terraform/demo-app/us-east-1` after approval in `consoletrack-revert` |

## Repository layout

```text
consoletrack/
├── AGENTS.md                  # instructions for coding agents (no CLAUDE.md, by design)
├── product-spec.md            # the full spec
├── openapi.yaml               # API contract: source of truth for backend and frontend
├── Makefile                   # every command: preflight, up, deploy, demo-*, revert, down
├── .env.example               # the only config file (copy to .env)
├── docker-compose.yaml        # local stack, no AWS
├── backend/                   # FastAPI API, intake worker, triage agent (Python, uv)
├── frontend/                  # React + Vite + TypeScript dashboard
├── mcp-server/
│   ├── k8s/                   # k8s-mcp: read-only Kubernetes tools
│   └── aws/                   # aws-mcp: read-only AWS tools, fixtures mode
├── terraform/                 # AWS only, one root per account and scope
│   ├── consoletrack/
│   │   ├── global/            # GitHub OIDC, CI roles, boundary, DNS zone, budget
│   │   └── us-east-1/         # VPC, NAT, EKS + managed add-ons, Pod Identity, intake
│   └── demo-app/
│       └── us-east-1/         # the demo app's AWS side: the drift target
├── helm/                      # everything inside Kubernetes
│   ├── helmfile.yaml.gotmpl   # every release, pinned versions, environments
│   ├── charts/                # consoletrack, demo-app, gateway-api-crds
│   ├── values/                # values for third-party charts
│   └── environments/          # eks, local; generated values from .env
├── scripts/                   # bootstrap, preflight, scenarios/
├── agent-hooks/               # guardrail hooks for the coding agent
├── agent-capabilities/        # reusable agent workflows (skills)
├── custom-agent/              # specialist subagents
├── docs/                      # architecture, permissions, extension pack, mockups
├── security/  ops/            # placeholders (Phase 2)
└── .github/                   # CI workflows, Dependabot, CODEOWNERS
```

## Roadmap

- **Phase 1 (Oct 26):** CloudTrail change intake with intake health and canary; change records, analysis, and Accept; gated Revert; Alertmanager incidents with the read-only `k8s-mcp` and `aws-mcp`; two cloud scenarios (security group opened, demo role permission removed) and three cluster faults; the dashboard; EKS via Terraform and Helmfile; CI/CD; the agent extension pack.
- **Phase 2 (Nov 16):** Codify (the agent drafts a Terraform PR adopting the change; CI must plan with no diff); attribute-level drift from `terraform plan -refresh-only`; harder identity scenarios (Pod Identity association, trust policy, KMS key policy); Slack and analytics; auto-remediation for the cluster OOM scenario after it graduates.
- **Later:** a NAT gateway per AZ, or fck-nat as a raw NAT instance under `/ct/` to cut the NAT cost; Claude through Bedrock over PrivateLink, ECR pull-through cache, private API endpoint with Argo CD, Argo CD out-of-sync events as a change source, multi-region and multi-account watching (hub and spoke).

## Course rubric mapping

Built for the [DataTalksClub AI Dev Tools Zoomcamp](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp) final project.

| # | Criterion | Where to look |
|---|---|---|
| 1 | Problem description | The opening of this README |
| 2 | AI-assisted workflow | [docs/ai-workflow.md](docs/ai-workflow.md), [AGENTS.md](AGENTS.md), [agent-hooks/](agent-hooks/) |
| 3 | Technologies and architecture | [How it works](#how-it-works), [product-spec.md](product-spec.md) |
| 4 | Frontend | [frontend/](frontend/): `src/api/` client generated from OpenAPI; `make test-frontend` |
| 5 | API contract | [openapi.yaml](openapi.yaml), contract test in CI |
| 6 | Backend | [backend/](backend/): layered FastAPI app with unit tests |
| 7 | Database | Alembic migrations; Postgres in Compose, Testcontainers, and CloudNativePG |
| 8 | Containerization | [docker-compose.yaml](docker-compose.yaml); images for every service |
| 9 | Integration testing | `backend/tests/integration/` (Testcontainers) and kind-based `make test-k8s` |
| 10 | Deployment | EKS via [terraform/](terraform/) and [helm/](helm/); live URL and dates below; [Deploy to AWS](#deploy-to-aws) |
| 11 | CI/CD | [.github/workflows/](.github/workflows/): `ci.yaml`, `deploy.yaml` |
| 12 | Agent extension pack | [Agent extension pack](#agent-extension-pack) |
| 13 | Security, audit, DevOps | [Security and permissions](#security-and-permissions), [docs/permissions.md](docs/permissions.md), [security/](security/) |
| 14 | Reproducibility | `make preflight` → `make up` → `make demo` → `make down` → `make verify-clean` |

<!-- TODO: live URL and the dates it will be up during peer review -->

## License

[Apache-2.0](LICENSE)
