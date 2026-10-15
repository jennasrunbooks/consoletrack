# ConsoleTrack

<!-- TODO badges: CI | Terraform | License -->
<!-- TODO screenshot: docs/mockups/change-log.png -->

Someone opens the AWS console (or CLI, CloudShell, an SDK script, or a local `terraform apply`), changes a security group, and moves on. Nobody records why. A week later a pod gets `AccessDenied` and the on-call engineer starts from scratch.

ConsoleTrack catches every out-of-band AWS change as it happens, records it as a change record, and links later EKS incidents back to the change that most likely caused them. A reviewer can **Revert** it (gated GitHub workflow re-applies Terraform), **Codify** it (open a PR, Phase 2), or **Accept** it with a note. The triage agent is read-only.

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

<!-- TODO architecture diagram: docs/architecture.png -->

1. **Intake.** CloudTrail → EventBridge rule (write events, excluding pipeline sessions `gh-*`) → SQS (with dead-letter queue).
2. **Change records.** The backend consumes the queue and stores each change in Postgres (CloudNativePG) with its principal, source, resource, and Terraform address.
3. **Incidents.** Kubernetes events (e.g. `AccessDenied`, `CrashLoopBackOff`) open an incident. The agent correlates it with recent change records using the `k8s-mcp` and `aws-mcp` servers.
4. **Review.** Revert, Codify, or Accept. AWS-side changes always need human approval.
5. **Intake health.** A `ct-canary` CronJob makes a known change every 15 minutes, so the header shows Live / Delayed / Down / Replay.

Full design: [product-spec.md](product-spec.md) · API: [openapi.yaml](openapi.yaml)

## Try it without AWS

Prerequisites: Docker, `make`, <!-- TODO kind version -->

```bash
git clone https://github.com/jennasrunbooks/consoletrack.git
cd consoletrack
cp .env.example .env        # set ANTHROPIC_API_KEY
make demo-replay            # Compose + ElasticMQ + recorded CloudTrail fixtures
# open http://localhost:<!-- TODO port -->
```

`make local` runs the same stack on a local kind cluster. In both modes, Revert and Codify are disabled ("AWS required").

## Deploy to AWS

### Prerequisites

- AWS account with admin access for the initial bootstrap
- Terraform >= 1.11, kubectl, Helm, AWS CLI v2 <!-- TODO pin versions -->
- A fork of this repo (the GitHub OIDC roles trust your repo)
- Anthropic API key
- Optional: a domain (`DOMAIN`, `DNS_PARENT=route53|external`), required only for `EXPOSURE=alb`

### Steps

```bash
cp .env.example .env        # fill in the values, see Configuration
make preflight              # checks tools, credentials, region, quotas, CAA
make bootstrap              # creates the Terraform state bucket (S3 native locking)
make up                     # terraform/consoletrack/global, us-east-1, demo-app; Helm releases
make open                   # port-forwards the UI (EXPOSURE=alb for a load balancer)
```

<!-- TODO: GitHub setup — environments (<account>-plan/-apply/-revert), AWS_ROLE_ARN environment secret per environment, branch protection -->

### Demo scenarios

```bash
make demo-drift             # opens port 22 to the internet on demo-app-sg (no outage)
make demo-identity          # removes s3:GetObject from the demo app role → AccessDenied incident
```

### Teardown

```bash
make down
make verify-clean           # confirms nothing billable is left
```

## Cost

About **$0.16/hour** with the defaults (EKS standard support, 2× Spot t4g.medium, fck-nat on t4g.nano, no load balancer). <!-- TODO table / link to spec --> Run `make down` when you're done.

## Configuration

`.env` is the only config file. The Makefile turns it into `TF_VAR_*` variables, backend settings, and Helm values.

| Variable | Required | Default | Description |
|---|---|---|---|
| `AWS_REGION` | yes | `us-east-1` | <!-- TODO --> |
| `NAME_SUFFIX` | yes | | Suffix for `ct-<component>-<suffix>` names |
| `GITHUB_REPO` | yes | | `owner/repo` trusted by the OIDC roles |
| `ANTHROPIC_API_KEY` | yes | | Triage agent |
| `EXPOSURE` | no | `port-forward` | `port-forward` or `alb` |
| `ALLOWED_CIDRS` | with `alb` | your IP | Source CIDRs allowed to reach the ALB |
| `DOMAIN` | with `alb` | | e.g. `consoletrack.example.com`; the ALB serves HTTPS only |
| `DNS_PARENT` | with `DOMAIN` | | `route53` or `external` |
<!-- TODO complete from .env.example -->

## Using ConsoleTrack

<!-- TODO screenshots + short walkthrough: change log, change detail, incident, intake panel -->

## Triage agent and MCP servers

- **Agent:** Claude via the Anthropic SDK; returns a structured `submit_analysis`.
- **`k8s-mcp`:** read-only Kubernetes tools. <!-- TODO tool list -->
- **`aws-mcp`:** read-only CloudTrail, IAM, and EC2 lookups; fixtures mode for demo-replay. <!-- TODO tool list -->
- **Remediation modes:** manual by default; auto only for cluster-side actions that have graduated (5 eval passes + PR).

## Agent extension pack

<!-- TODO summary + links -->
See [docs/agent-extension-pack.md](docs/agent-extension-pack.md): `agent-capabilities/`, `agent-hooks/`, `mcp-server/`, `plugins/`.

## Security and permissions

- No long-lived AWS keys: GitHub Actions uses OIDC roles (plan, apply, revert, deploy), and pods use EKS Pod Identity.
- Least-privilege IAM under the `/ct/` path with a permissions boundary.
- Apply and Revert require approval in the `consoletrack-apply` and `consoletrack-revert` GitHub environments.
- Private nodes; the API endpoint is public, restricted with `ALLOWED_CIDRS` <!-- TODO confirm -->

Details: [docs/permissions.md](docs/permissions.md) · [security/](security/)

## Development

See [AGENTS.md](AGENTS.md) for conventions used by humans and coding agents.

```bash
make help                   # lists every target
make test
make lint
```

## Testing and CI/CD

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yaml` | PR, push | lint, unit + integration tests, image build |
| `terraform.yaml` | PR / merge touching `terraform/**` | per-account matrix: plan on PR, apply on `main` after `<account>-apply` approval |
| `revert.yaml` | Revert button | re-applies `terraform/demo-app/us-east-1` after `revert` approval |
| `deploy.yaml` | merge to `main` | pushes arm64 images to GHCR, Helm upgrade |

## Repository layout

```text
backend/  frontend/  mcp-server/
terraform/
  consoletrack/{global,us-east-1}/
  demo-app/us-east-1/
helm/{consoletrack,demo-app,addons}/
scripts/  docs/  security/  ops/
agent-capabilities/  agent-hooks/  plugins/
.github/workflows/
```

## Roadmap

- **Phase 1 (Oct 26):** <!-- TODO -->
- **Phase 2 (Nov 16):** Codify PRs, <!-- TODO -->
- **Later:** multi-region forwarders, hub-and-spoke accounts, Argo CD drift source, Bedrock over PrivateLink

## Course rubric mapping

Built for the [DataTalksClub AI Dev Tools Zoomcamp](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp) final project.

| Criterion | Where |
|---|---|
| <!-- TODO 14 rows --> | |

## License

[Apache-2.0](LICENSE)
