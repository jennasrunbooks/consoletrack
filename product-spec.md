# ConsoleTrack: Product Spec

Last updated Oct 15, 2026

## Summary

ConsoleTrack turns undocumented cloud changes into documented, reviewable records. It catches console ("ClickOps") changes and Terraform drift within minutes, and when a workload on EKS breaks because of one, it traces the Kubernetes symptom back to the AWS change that caused it. The agent only reads; fixes arrive as Terraform pull requests or approved, gated workflow runs.

**Problem.** Developers with console access make quick changes to unblock themselves: a policy tweak, an opened security group port, a bumped limit. Nobody records why, Terraform state drifts, and the next `terraform apply` either silently reverts the change or fails. When an outage follows, the on-call engineer sees `AccessDenied` in pod logs and has to search CloudTrail by hand to find who changed what. In-cluster AI agents don't look at the AWS layer, and managed agents investigate incidents but don't treat drift as the thing to track.

**What the system does.**

1. **Change-driven.** Any write API call to a Terraform-managed resource by a principal other than the Terraform or CI role flows through CloudTrail → EventBridge → SQS and opens a change record within minutes, even if nothing is broken yet.
2. **Symptom-driven.** A Prometheus alert (an `AccessDenied` error rate, a CrashLoopBackOff) opens an incident. The agent gathers pod evidence and looks for recent out-of-band changes to the AWS resources that workload depends on.
3. The agent writes a plain-language summary: who made the change, what changed, when, from the console or CLI, how it differs from Terraform, the risk it introduces, and the root cause when there is a symptom.
4. A reviewer picks **Revert** (re-apply Terraform through a gated workflow), **Codify** (the agent drafts a pull request that adopts the change in Terraform, checked for least privilege), or **Accept with a note**.
5. Every decision lands in a change log, so the unspoken change ends up documented and linked to any incident it caused.

**Who it's for.** Platform and SRE teams who share AWS accounts with application developers, manage infrastructure with Terraform, and haven't fully locked down console access (which is most teams). For this project it is also a portfolio piece covering EKS, IAM and Pod Identity, CloudTrail and EventBridge, Terraform, MCP, and governed agent actions.

**Expected behavior in the demo.** `make demo-drift`: a "developer" role opens port 22 to the internet on a security group; a few minutes later a change record shows who did it, the risk, and Revert or Codify options. `make demo-identity`: the same role removes `s3:GetObject` from the demo app's IAM role policy; the app starts failing, an alert fires, and the incident links straight to that change; Revert restores service. The cluster-only faults (OOM, bad image, bad probe) remain as a baseline.

**How this differs from existing tools.** HolmesGPT and K8sGPT reason mainly inside the cluster and its observability stack. AWS DevOps Agent is a managed, per-second-billed incident investigator. None of them makes the out-of-band cloud change the primary object, documents it, and reconciles it with Terraform through least-privilege, human-approved actions.

## Name

The project is ConsoleTrack: it keeps track of every console change, documents it, and ties it back to Terraform. It names the problem rather than one feature, so it still fits as the suite grows into network and other domains. The same name is used everywhere below.

| Thing | Name |
| --- | --- |
| Product | ConsoleTrack |
| GitHub repo | `jennasrunbooks/consoletrack` |
| Python package | `consoletrack` (PyPI name unclaimed as of Oct 13, 2026) |
| Helm chart and namespace | `consoletrack` |
| Container images | `ghcr.io/jennasrunbooks/consoletrack-backend` (also runs the worker), `-frontend`, `-k8s-mcp`, `-aws-mcp`, `-demo-app` |
| AWS resource name prefix | `ct-` on every AWS resource ConsoleTrack owns (the hub infrastructure, its roles, the canary, and the scenario tooling), followed by the component and a short random suffix: `ct-<component>-<suffix>`, for example `ct-eks-a1b2`, `ct-tfstate-a1b2`, `ct-canary-sg-a1b2`. The demo app is a watched workload, not ConsoleTrack, so its resources use `demo-app-` instead (`demo-app-sg-a1b2`, `demo-app-data-a1b2`), matching its directory and future account. Each root sets its prefix once in `local.name_prefix` |
| AWS tags | `project = consoletrack`, `owner = <suffix>` |
| IAM path | `/ct/` for ConsoleTrack's IAM roles and policies and `/demo-app/` for the demo app's. Policies scope ARNs by these paths (`arn:aws:iam::<account>:role/ct/*`, `role/demo-app/*`), so ConsoleTrack's own permissions and the workload it watches never overlap |

## Scope

Phase 1 targets the Monday, Oct 26 submission (6 pm local time): every graded criterion plus the core cloud loop (change intake, two cloud scenarios, gated revert). Phase 2 targets the Nov 16 fallback deadline and the post-course version. On Oct 22 we check progress: if Phase 1's cloud items are not on track, we submit on Nov 16 instead of cutting the drift features.

| Phase | Feature | Why it's in this phase |
| --- | --- | --- |
| 1 (Oct 26) | Alertmanager webhook → incident; triage agent with read-only `k8s-mcp` | Symptom-driven trigger; core AI capability |
| 1 (Oct 26) | CloudTrail trail + EventBridge rule + SQS → change records for out-of-band writes, plus intake health endpoint, header indicator, and canary | Change-driven trigger; the main differentiator |
| 1 (Oct 26) | Read-only `aws-mcp`: CloudTrail lookup, IAM read and policy simulation, security group read, Terraform state lookup | Lets the agent connect a pod symptom to an AWS change |
| 1 (Oct 26) | Scenarios: security group opened (no outage) and demo app role permission removed (outage) | One demo per trigger |
| 1 (Oct 26) | Revert through a gated GitHub workflow that re-applies the `terraform/demo-app/us-east-1` root | Fixes cloud drift without giving the app AWS write access |
| 1 (Oct 26) | Dashboard: incidents, change log, evidence, approve/reject, postmortem editor | Frontend criterion; makes changes documented |
| 1 (Oct 26) | Cluster-side remediation (patch, rollout undo) with policy model set to `manual` | Baseline faults and the auto-remediation path |
| 1 (Oct 26) | Terraform + Helm on EKS, CI/CD, reviewer deploy, agent extension pack | Graded criteria |
| 2 (Nov 16) | Codify: agent drafts an HCL pull request adopting the change; CI plan must show no diff | Turns a good console change into code |
| 2 (Nov 16) | Drift confirmation with a targeted `terraform plan -refresh-only` job | Exact attribute-level diff instead of request parameters |
| 2 (Nov 16) | Scenarios: demo app's Pod Identity association or role trust policy changed; KMS key policy edited | Harder identity failures |
| 2 (Nov 16) | Slack notifications; analytics (changes by principal, time to document, drift reverted vs codified) | Team visibility |
| 2 (Nov 16) | Auto-remediation for the cluster OOM scenario | Shows the graduated autonomy path |
| Later | Network-path incidents (security groups, ALB target health, DNS, IP exhaustion) | Next domain, built on the same change intake |
| Later | Production hardening path (managed NAT, Bedrock over PrivateLink, ECR pull-through cache, private API endpoint with Argo CD); watching more regions through EventBridge forwarding; Argo CD out-of-sync events as a change source; multi-account via an organization trail; headless Claude Code runner; commit tracing; security criterion artifacts | Beyond the course |

Phase 1 describes drift from CloudTrail request parameters plus the Terraform address of the resource. The exact attribute diff from `terraform plan` arrives in Phase 2.

**Out of scope.** Multi-cluster support, auth beyond a single shared admin login, auto-remediation of any AWS-side change (cloud fixes always need a human), drift in resources Terraform doesn't manage, and production-grade HA.

## Architecture

Two inputs feed one agent: Prometheus alerts from inside the cluster, and CloudTrail change events from the AWS account. There are exactly two write paths, both behind an approval: the backend's cluster patch, and a GitHub workflow that re-applies Terraform.

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

The triage worker sees Kubernetes only through `k8s-mcp` and AWS only through `aws-mcp`, each with read-only permissions. AWS changes are fixed by the revert workflow in GitHub, whose role can change only the `terraform/demo-app/us-east-1` root and only after a reviewer approves the run.

| Component | Tech | Role |
| --- | --- | --- |
| Frontend | React, Vite, TypeScript, nginx | Incidents, change log, evidence, approve/reject, postmortem editor; light and dark themes from CSS variables, defaulting to the OS setting with a header toggle that is remembered per browser |
| Backend API | Python 3.12, FastAPI, SQLAlchemy, Alembic | Webhook intake, state machines, remediation validation, cluster patch, triggers revert workflow |
| Triage worker | Same image as backend, Anthropic SDK, FastMCP client, boto3 | Consumes the SQS change queue and runs agent loops for changes and incidents |
| k8s-mcp | FastMCP, kubernetes client | Read-only cluster tools |
| aws-mcp | FastMCP, boto3, own Pod Identity role | Read-only CloudTrail, IAM, EC2, and Terraform state tools |
| Change intake | CloudTrail trail, EventBridge rule, SQS | Delivers out-of-band write events within minutes |
| Demo app | Small Python service using Pod Identity to read from S3 | Gives the identity scenario a real AWS dependency |
| Postgres | CloudNativePG (EKS), Postgres 16 container (local) | Incidents, changes, evidence, analyses, remediations |
| Observability | kube-prometheus-stack | Alert rules, Alertmanager webhook to backend |
| Infrastructure | Terraform modules, Helm | `terraform/consoletrack/global` (IAM and GitHub OIDC, Route 53 zone, optional budget), `terraform/consoletrack/us-east-1` (private VPC with NAT instance, EKS, Pod Identity roles, intake), and `terraform/demo-app/us-east-1` (demo IAM, S3, security group); settings from `.env` |
| CI/CD | GitHub Actions with AWS OIDC | Test, build, deploy, plan on PRs, apply on merge, gated revert of the demo-app root |

## AWS design and cost

The cluster runs only when needed, with private nodes and nothing public by default: about $0.16 an hour while up (about $0.19 with the optional ALB), roughly $10 for 60 demo and test hours, versus about $115 a month if left on. The EKS control plane is the biggest line, so the main lever is still `terraform destroy` when idle.

**Terraform layout.** Code is organized by AWS account first, then by scope (global or a region), one root per folder, with one file per concern (`eks.tf`, `vpc.tf`, `iam.tf`, and so on) inside each root. Each top-level directory under terraform/ is named for the AWS account it targets. For the course, everything runs in one account, the ConsoleTrack hub, with one shared demo setup.

- `terraform/consoletrack/` holds everything in the ConsoleTrack hub account:
  - `global/` (account-wide and long-lived, pennies a month): GitHub OIDC provider and the plan, apply, deploy, and revert roles (only when `enable_github_oidc = true`; reviewers leave it off), the permissions boundary, the demo "developer" role, the Route 53 zone when `DOMAIN` is set, and an optional AWS Budgets alert at $25. The alert is controlled by `enable_budget`: on by default for reviewers with standalone accounts, and off for the author, whose billing, budgets, and Cost Explorer live only in the AWS Organizations management account. Files: `iam.tf`, `route53.tf`, `budgets.tf`.
  - `us-east-1/` (regional platform, created and destroyed freely, about 15 to 20 minutes each way): `vpc.tf` (private subnets, S3 gateway endpoint), `nat.tf` (fck-nat instance), `eks.tf` (cluster, Spot node group, add-ons, access entries), `pod-identity.tf`, `intake.tf` (CloudTrail trail and log bucket, EventBridge rule, SQS queue), `acm.tf`, and `addons.tf` (Helm releases for third-party charts). Pod Identity roles live here, next to the cluster they bind to, even though IAM is global.
- `terraform/demo-app/us-east-1/` (applies in under a minute): the AWS side of the demo app, matching `helm/demo-app` for its Kubernetes side. That is its IAM role and policy, S3 bucket, security group, and Pod Identity association: the resources the demo scenarios drift on purpose and the revert workflow re-applies. The directory is named for the `demo-app` spoke account it belongs in; to save time it deploys into the ConsoleTrack account for the course, so moving it later changes the provider's target account, not the layout. Keeping it in its own state is what lets the revert role touch nothing else.

Configuration comes from one place: `.env`. The Makefile turns it into `TF_VAR_*` variables, the backend settings (state bucket name with its random suffix), and a generated Helm values file, so there are no tfvars or per-environment folders to keep in sync. The S3 state bucket itself is created once by `scripts/bootstrap-state.sh` (AWS CLI: versioned, encrypted, public access blocked), so no Terraform root has to manage its own backend.

State locking uses S3's native lockfile: every root's backend sets `use_lockfile = true`, and there is no DynamoDB lock table, the legacy approach that Terraform now deprecates. Each root's state key mirrors its directory (`consoletrack/global/terraform.tfstate`, `consoletrack/us-east-1/terraform.tfstate`, `demo-app/us-east-1/terraform.tfstate`), and the lock is a `.tflock` object written next to it, which is why the plan role may write only `*.tflock` keys. Bucket versioning stays on so a bad state write can be rolled back.

**Implementation notes for the roots.**

- The demo app's Pod Identity association lives in `terraform/demo-app/us-east-1`, not in the `consoletrack` roots. The Phase 2 identity scenario breaks that association, so Revert must be able to recreate it, and the revert role carries `eks:CreatePodIdentityAssociation`, `eks:DeletePodIdentityAssociation`, and `eks:DescribePodIdentityAssociation` scoped to the cluster and the demo app's service account.
- `terraform/demo-app/us-east-1` finds the cluster and anything else it needs from the `consoletrack` roots through data sources looked up by name (`aws_eks_cluster`, for example), never by reading their remote state. The roots stay loosely coupled, which is also what lets `demo-app` move to its own account later, and destroy order is handled by `make down`.
- `.env` locally and GitHub secrets and variables in CI use identical names, and CI runs the same Makefile targets with them, so local and CI behavior can't diverge. `make preflight` fails fast with the name of any required variable that is missing. Helm values come from a gitignored values file the Makefile generates from `.env`, not a long chain of `--set` flags.

The S3 state bucket is created once by `scripts/bootstrap-state.sh` (AWS CLI: versioned, encrypted, public access blocked), so no Terraform root has to manage its own backend.

**Cost choices.**

| Choice | Instead of | Why |
| --- | --- | --- |
| Nodes in private subnets, egress through one fck-nat instance (t4g.nano) in a public subnet | Public nodes, or a managed NAT gateway | Nodes get no public IPs. fck-nat costs about $3 a month with no per-GB charge, against $0.045/hr plus $0.045/GB for a managed NAT gateway. One AZ is fine for a demo; production would use a managed NAT gateway per AZ |
| S3 gateway endpoint | S3 traffic through NAT | Free; covers the state bucket, CloudTrail logs, and the demo bucket |
| No interface endpoints | ECR, STS, SQS interface endpoints | About $7.30 a month each per AZ, and nodes still need internet egress for the Claude and GitHub APIs, so the NAT instance is cheaper |
| Graviton everywhere: t4g.medium Spot nodes, t4g.nano NAT, arm64 images | x86 | Spot is about half of on-demand and Graviton is cheaper per vCPU; arm64 also matches an Apple Silicon Mac |
| Latest standard-support Kubernetes version, upgrade policy STANDARD | Default policy | Avoids the $0.60/hr extended-support rate; 1.34 leaves standard support on Dec 2, 2026 |
| No load balancer by default: the chart ships Gateway API resources, and `make open` reaches the dashboard through `kubectl port-forward` | An always-on ALB | Saves about $0.035/hr and leaves nothing exposed. `EXPOSURE=alb` provisions one ALB through the Load Balancer Controller's Gateway API support, limited to `ALLOWED_CIDRS`, for demo day or reviewers who want a URL |
| Postgres in-cluster via CloudNativePG, gp3 volume | RDS | Destroyed with the cluster; a seed script restores demo data |
| kube-prometheus-stack with short retention, Grafana on, extra exporters off | Managed Prometheus/Grafana | No per-metric or per-user fees |
| EKS Pod Identity for every pod that calls AWS | IRSA, node-role permissions, static keys | Current EKS standard, no per-cluster OIDC provider, and supported by `terraform-aws-modules/eks-pod-identity` |

**Hourly estimate while the cluster is up (us-east-1, list prices, October 2026).**

| Item | $/hour |
| --- | --- |
| EKS control plane, standard support | 0.100 |
| 2× t4g.medium Spot (about $0.016 to $0.021 each) | 0.040 |
| fck-nat on t4g.nano plus its public IPv4 | 0.009 |
| EBS gp3, about 60 GB total | 0.007 |
| **Total, default (no load balancer)** | **about 0.16** |
| Optional ALB with two public IPv4 addresses | +0.035 |

Outside the cluster: Claude API usage is metered per triage, so set a monthly spend limit on the API key and default the triage agent to a Haiku-class model, with a Sonnet-class model as a per-incident option.

ConsoleTrack is hosted in us-east-1 because of where AWS delivers change events, not because it can only watch one region. Two rules decide it. Global services such as IAM deliver their CloudTrail events to EventBridge only in us-east-1. Regional services (EC2 security groups, S3, KMS, EKS) deliver them to EventBridge in the region where the change happened. In both cases an EventBridge rule only sees these events when a trail covers its region ([AWS knowledge center](https://aws.amazon.com/premiumsupport/knowledge-center/iam-cloudwatch-sns-rule/), [EventBridge docs](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-service-event-cloudtrail.html)). Hosting the intake in us-east-1 therefore catches IAM changes plus regional changes in us-east-1, which covers every demo resource. Watching other regions is a later addition, described under the production hardening path. The intake adds almost nothing to the bill: the first copy of management events in a trail is free, a few weeks of logs in S3 cost cents, and SQS polling stays within the free tier.

Prices: [EKS pricing](https://aws.amazon.com/eks/pricing/), [public IPv4 charge](https://aws.amazon.com/blogs/aws/new-aws-public-ipv4-address-charge-public-ip-insights), [ALB pricing summary](https://cloudburn.io/blog/aws-elastic-load-balancing-pricing), [t4g.medium Spot](https://calculator.holori.com/aws/ec2/t4g.medium/us-east-1), [EKS 1.34 support dates](https://www.cloudmagazin.com/en/2026/09/22/kubernetes-support-ends-amazon-charges-six-times-the-price/).

Network and ingress references: [fck-nat cost comparison](https://pypi.org/project/cdk-fck-nat), [interface endpoint cost](https://dev.to/enforza/how-to-reduce-aws-nat-gateway-costs-7-ways-ranked-by-savings-ll4), [Load Balancer Controller Gateway API GA](https://www.infoq.com/news/2026/03/aws-gateway-api-ga), [Gateway API implementations](https://gateway-api.sigs.k8s.io/docs/implementations/).

**Custom domain (optional, for any deployment).** Anyone deploying ConsoleTrack can serve the dashboard over HTTPS at a hostname they own by setting `DOMAIN` (for example `consoletrack.example.com`) together with `EXPOSURE=alb`. The author's deployment uses `consoletrack.jennasrunbooks.com`. Leaving `DOMAIN` empty keeps `make open` or the ALB's own DNS name, and nothing in the code, chart, or Terraform hardcodes a domain.

| Piece | Where | How |
| --- | --- | --- |
| DNS zone | `terraform/consoletrack/global/route53.tf`, `terraform-aws-modules/route53/aws` | A Route 53 public hosted zone for `DOMAIN` only. `DNS_PARENT` picks how it's delegated: `route53` when the parent domain's zone is in the same AWS account, so Terraform adds the NS records itself; `external` (the default) for any other registrar or DNS host, such as Namecheap, Cloudflare, or GoDaddy, where `make domain-zone` prints the four NS records to add by hand. The parent domain and its other records never move. About $0.50 a month |
| TLS certificate | `terraform/consoletrack/us-east-1/acm.tf`, `terraform-aws-modules/acm/aws` | Public ACM certificate for `DOMAIN`, DNS-validated in that zone. Free. It is re-issued on each `make up` and validates in a few minutes because the zone persists. The Load Balancer Controller attaches it to the HTTPS listener, and HTTP redirects to HTTPS |
| DNS record | ExternalDNS in the cluster | Watches the Gateway's HTTPRoute and points the hostname at whichever ALB the current cluster has, so a rebuild needs no manual DNS change |
| ExternalDNS identity | Pod Identity, `eks-pod-identity` module's ExternalDNS policy | `route53:ChangeResourceRecordSets` on that one zone's ARN, plus list and read on zones; a `txtOwnerId` so it never touches records it didn't create |

Setup is once per domain. With `DNS_PARENT=route53`, `make domain` does everything in one run. With `external` it takes two steps, because the certificate can't validate until the parent delegates the subdomain: `make domain-zone` applies the zone in `terraform/consoletrack/global` and prints the host, record type, and four name servers to add at your DNS provider; `make domain-cert` waits until `dig NS $DOMAIN` returns them, then requests and validates the certificate. If the parent domain has CAA records, one must allow `amazon.com`, and `make preflight` checks for that. The zone lives in `terraform/consoletrack/global`, so it survives `make down`; the certificate lives with the regional platform and comes back automatically on the next `make up`. `make domain-destroy` removes the zone and reminds you to delete the NS records at your provider. The README walks through both paths, using the author's setup (four NS records in Namecheap's Advanced DNS with host `consoletrack`) as the `external` example.

A public URL changes who can reach the login page, so the MVP adds a read-only **viewer** account alongside the admin login. Peers can browse changes, evidence, and incidents; Revert, Accept, and Approve need the admin login. `ALLOWED_CIDRS` can be opened to `0.0.0.0/0` for the review window only. The URL answers only while the cluster is up, so the README states the dates it will be live and shows screenshots of the deployment for the rest of the time.

## Infrastructure as code

All AWS resources come from community-maintained terraform-aws-modules, pinned to exact versions; hand-written resources are limited to small glue the modules don't cover.

| Need | Module | Custom settings this project relies on |
| --- | --- | --- |
| VPC | `terraform-aws-modules/vpc/aws` | 2 AZs; private subnets for nodes; public subnets only for the NAT instance and the optional ALB; `enable_nat_gateway = false`; S3 gateway endpoint; ELB subnet tags |
| NAT instance | `RaJiska/fck-nat/aws` | t4g.nano (Graviton), single AZ, routes for both private route tables, HA mode off for the demo |
| EKS cluster and node group | `terraform-aws-modules/eks/aws` (v21, AWS provider 6) | API authentication mode with access entries; `enable_irsa = false`; Spot t4g.medium managed node group in private subnets; add-ons vpc-cni, coredns, kube-proxy, aws-ebs-csi-driver, and eks-pod-identity-agent (installed before nodes); public and private endpoint access; upgrade policy STANDARD |
| Pod Identity roles | `terraform-aws-modules/eks-pod-identity/aws` | Built-in policy switches for the Load Balancer Controller, EBS CSI, and VPC CNI; custom policies for `aws-mcp`, the worker's SQS access, and the demo app's S3 read; one association per `namespace:serviceaccount` |
| GitHub OIDC | `terraform-aws-modules/iam/aws`, OIDC provider and role submodules | Plan role for pull requests, apply role for `main` behind the `consoletrack-apply` environment, app deploy role, revert role |
| CloudTrail trail and log bucket | `cloudposse/cloudtrail/aws` and `cloudposse/cloudtrail-s3-bucket/aws` | Single-region trail in us-east-1, write management events, global service events on, short log lifecycle |
| EventBridge rule | `terraform-aws-modules/eventbridge/aws` | Pattern on `AWS API Call via CloudTrail` for IAM, EC2, S3, KMS, and EKS (Pod Identity association changes), excluding the Terraform and CI role ARNs |
| SQS queue | `terraform-aws-modules/sqs/aws` | Dead-letter queue, queue policy allowing only the EventBridge rule to send |
| State bucket | `terraform-aws-modules/s3-bucket/aws` | Versioning, encryption, public access blocked |
| Cluster add-ons | `hashicorp/helm` provider with upstream charts | Gateway API CRDs, kube-prometheus-stack, cloudnative-pg; aws-load-balancer-controller with Gateway API enabled only when `EXPOSURE=alb` |

The app chart itself is deployed by `make deploy` or CI with `helm upgrade`, not by Terraform, so app releases don't need a Terraform run. Module versions are pinned in `versions.tf` and updated by Dependabot PRs.

Pod Identity replaces IRSA throughout. Each role trusts `pods.eks.amazonaws.com` and is bound to one namespace and service account by an EKS association, so roles don't depend on a cluster's OIDC issuer and survive cluster rebuilds unchanged. Pods need the eks-pod-identity-agent add-on and a recent AWS SDK (boto3 qualifies); the agent calls the EKS Auth API, which private nodes reach through the NAT instance. Because associations live in EKS, the EventBridge rule also watches EKS association changes, which is what the Phase 2 identity scenario breaks. One item to confirm at build time: that the chosen VPC CNI add-on version accepts a Pod Identity association. If it doesn't, the CNI stays on the node role as the single documented exception.

Module references: [EKS module v20 upgrade notes on access entries](https://github.com/terraform-aws-modules/terraform-aws-eks/blob/master/docs/UPGRADE-20.0.md), [v21 upgrade walkthrough](https://dev.to/setevoy/terraform-aws-eks-terraform-module-update-from-version-20x-to-version-21x-52im), [eks-pod-identity module](https://registry.terraform.io/modules/terraform-aws-modules/eks-pod-identity/aws/latest), [fck-nat](https://github.com/AndrewGuenther/fck-nat).

## Production hardening path

The demo trades a few production properties for cost. These upgrades change only the platform around ConsoleTrack, not the application, and are listed in the order they'd be adopted.

| Step | Change | What it buys | Cost impact |
| --- | --- | --- | --- |
| 1. Managed NAT gateways | Replace the fck-nat instance with a managed NAT gateway in each AZ | AWS-operated, highly available egress with no instance to patch; the standard choice for productionized workloads | About $0.045/hr per AZ plus $0.045/GB processed |
| 2. Claude through Amazon Bedrock over PrivateLink | The worker calls Claude through Bedrock using a `bedrock-runtime` interface endpoint, with `bedrock:InvokeModel` on specific model or inference-profile ARNs granted through Pod Identity | Model traffic never leaves the AWS network, there is no API key to store or rotate, and every call is IAM-authorized and logged in CloudTrail | Interface endpoint per AZ plus Bedrock usage pricing |
| 3. Images through an ECR pull-through cache | ECR caches the GHCR images; nodes pull through ECR interface endpoints and the S3 gateway endpoint | No image pulls over the internet; ECR image scanning | ECR storage plus endpoints |
| 4. Private API endpoint with Argo CD | Turn off public access to the EKS API; Argo CD in the cluster pulls the Helm chart from Git instead of CI pushing with `helm upgrade` | Removes the main remaining exposure; deploys become declarative, auditable, and self-healing | Self-managed Argo CD runs on the existing nodes; EKS also offers a managed Argo CD capability billed per application-hour ([EKS capabilities pricing](https://komodor.com/learn/amazon-eks-pricing-quick-guide-and-6-ways-to-cut-eks-costs/)) |
| 5. Egress allowlisting | Limit what's left (GitHub, for Argo CD and the revert dispatch) with AWS Network Firewall domain rules | Nodes can reach only the endpoints they need | Network Firewall hourly; since Feb 6, 2026 AWS waives the NAT gateway hourly fee for gateways behind Network Firewall ([details](https://dev.to/enforza/how-to-reduce-aws-nat-gateway-costs-7-ways-ranked-by-savings-ll4)) |

After steps 1 to 4, GitHub is the only external dependency, and step 5 narrows egress to it. Steps 2 and 4 also show the identity story end to end: no static credentials anywhere, from the model call to the deploy.

**Hub and spoke accounts.** The course version runs everything in one AWS account, the ConsoleTrack hub, configured from `.env`, but `terraform/` is already laid out by account. The intended model is hub and spoke: `consoletrack` hosts the app and the intake, and each application team's account is a spoke, starting with `demo-app`. Moving `demo-app` into its own account means:

1. The spoke gets its own GitHub OIDC provider and plan, apply, and revert roles in terraform/demo-app/global/, and it joins the terraform.yaml matrix with its own GitHub environments instead of using the hub's roles.
2. The spoke's CloudTrail events reach the hub through an organization trail, or through a forwarding EventBridge rule targeting the hub's event bus.
3. `aws-mcp` and the revert workflow assume narrow read and revert roles in the spoke rather than using local permissions.
4. The demo app runs on a cluster in the spoke, so its Pod Identity association moves with it.

Separate environments follow the same pattern, one account each (for example `consoletrack-prod`), with shared code pulled into `terraform/modules/` or Terragrunt so accounts don't copy each other.

**Watching more regions.** The intake stays in us-east-1, where IAM events arrive, and other regions forward their regional events to it:

1. The trail becomes a multi-region trail. It is still one trail, and the first copy of management events stays free.
2. Each watched region gets a small EventBridge rule, with the same pattern and exclusions as the main one, whose target is a central event bus in us-east-1. That bus feeds the existing SQS queue, so the worker, change records, and UI don't change.
3. `aws-mcp` becomes region-aware. CloudTrail event history is per region, so lookups query the resource's own region (taken from its ARN or the event), plus us-east-1 for IAM.
4. Configuration is a `WATCH_REGIONS` list in `.env`. Coverage grows as a grid of accounts and regions, one directory each: a watched region gets a small forwarding rule in its own account's root, for example `terraform/demo-app/eu-west-1/`, never in the hub. Every account's `us-east-1/` root always carries a forwarder, because each account's IAM events arrive there. All forwarders target the central event bus in `terraform/consoletrack/us-east-1`, so adding coverage adds a directory and never changes the hub, the worker, or the UI. With AWS Organizations, one organization trail in the hub can replace per-account trails, while EventBridge forwarding still handles real-time delivery.

Cross-region event delivery is billed per event, which is negligible at drift-detection volumes. The intake indicator then lists every watched region, and the canary runs in each one so a broken forwarder shows up as Delayed or Down for that region.

**Argo CD as a ConsoleTrack feature.** Argo CD already detects drift for Kubernetes objects (someone runs `kubectl edit` in production). A future version can ingest Argo CD out-of-sync events as change records alongside CloudTrail, so cluster-side and AWS-side out-of-band changes land in the same change log with the same Revert, Codify, or Accept workflow.

## Identity and access

Every principal gets its own identity with the smallest permission set that works: Pod Identity for pods that call AWS, GitHub OIDC for automation, EKS access entries for people and CI, and namespaced Kubernetes Roles for the app. No pod uses the node role's permissions, and no long-lived AWS keys exist anywhere.

| Principal | Identity mechanism | Allowed | Explicitly not allowed |
| --- | --- | --- | --- |
| VPC CNI (`aws-node`) | Pod Identity (node role only if the add-on version can't use it) | `AmazonEKS_CNI_Policy` | CNI policy is removed from the node role |
| EBS CSI controller | Pod Identity | `AmazonEBSCSIDriverPolicy` | Anything outside EBS |
| AWS Load Balancer Controller (only with `EXPOSURE=alb`) | Pod Identity | Module's controller policy | Anything outside ELB, EC2 security groups and tags |
| Worker nodes | EC2 instance profile | `AmazonEKSWorkerNodePolicy`, ECR read-only for add-on images | CNI, EBS, ELB permissions |
| `aws-mcp` | Pod Identity | `cloudtrail:LookupEvents`; IAM get/list and `SimulatePrincipalPolicy` under the watched IAM paths (`/demo-app/` for the course, set by `WATCHED_IAM_PATHS`); `eks:DescribePodIdentityAssociation`; `ec2:DescribeSecurityGroups`; read of the demo-app root's state object | Any write; IAM outside the project path; other state files |
| Triage worker | Pod Identity | Receive and delete on the change queue; sqs:GetQueueAttributes on the change and dead-letter queues; cloudtrail:GetTrailStatus on the ConsoleTrack trail | Every other AWS API |
| Demo app | Pod Identity | `s3:GetObject` on its demo bucket (the permission the scenario removes) | Anything else |
| Frontend, backend | No AWS role | Nothing in AWS | Any AWS API |
| `k8s-mcp` ServiceAccount | Kubernetes Role in each allowlisted namespace | get, list, watch on pods, pods/log, events, deployments, replicasets, statefulsets | Secrets, ConfigMaps, exec, any write verb |
| `remediator` ServiceAccount (backend) | Kubernetes Role in each allowlisted namespace | get, patch on deployments; get, list on replicasets | create, delete, exec, Secrets, cluster-scoped resources |
| GitHub token held by backend | Fine-grained token for `jennasrunbooks/consoletrack` | Dispatch the revert workflow; open pull requests (Phase 2) | Merging, admin, any other repo |
| Terraform plan role | GitHub OIDC, trust limited to this repo's `consoletrack-plan` environment, used by pull request runs | Read-only describe and list on project resources, read state, write only the lock file | Any resource change; fork PRs get no token |
| Terraform apply role | GitHub OIDC, trust limited to `main` in the `consoletrack-apply` environment, which requires your approval | Apply the `terraform/consoletrack/global`, `terraform/consoletrack/us-east-1`, and `terraform/demo-app/us-east-1` roots under a permissions boundary | Resources outside the `ct-` and `demo-app-` prefixes, their IAM paths, and the project tags; IAM beyond the boundary |
| Revert workflow role | GitHub OIDC, trust limited to the `consoletrack-revert` environment, which requires a reviewer | Apply the `terraform/demo-app/us-east-1` root only, including the demo app's Pod Identity association | The `consoletrack` roots and anything with the `ct-` prefix or `/ct/` path; anything outside the `demo-app-` prefix and `/demo-app/` path |
| App deploy role | GitHub OIDC → IAM role + EKS access entry mapped to a namespaced Role | `eks:DescribeCluster`; Helm upgrade in the `consoletrack` namespace | Cluster admin, any AWS change |
| Demo "developer" role | Assumed by `make demo-*` to simulate ClickOps | Edit the demo app's policy and security group only | Everything else; exists only for the scenarios |
| Operator running Terraform (you or a reviewer) | Their own IAM identity + EKS access entry | Cluster admin, needed to install add-ons | Documented as the only admin |

Two permissions can't be narrowed by resource: `cloudtrail:LookupEvents` and `ec2:DescribeSecurityGroups` are account-wide reads, documented as an accepted gap. People and CI reach the cluster through access entries, the EKS-native replacement for the `aws-auth` ConfigMap. The API endpoint keeps public access so GitHub-hosted runners can deploy, authenticated by IAM; nodes use the private endpoint. If deploys later move to a pull-based model, restricting public access to `ALLOWED_CIDRS` is a one-variable change. `docs/permissions.md` holds this table plus the RBAC manifests, and `make audit-rbac` prints effective permissions with `kubectl auth can-i --list` for each ServiceAccount.

**Development-time access.** The Claude dev agent works on `jennasrunbooks/consoletrack` with its own fine-grained PAT, separate from the runtime token the backend uses.

| Setting | Value | Why |
| --- | --- | --- |
| Resource owner | `jennasrunbooks` org, this one repository only | The org must allow fine-grained PATs; you approve the token as org owner |
| Contents | Read and write | Push feature branches |
| Pull requests | Read and write | Open and update PRs |
| Issues, Actions, Metadata | Read | Follow CI results and the task list |
| Workflows | None | The agent can't edit `.github/workflows/`, so CI, deploy, and revert pipelines only change through your own commits |
| Administration, Secrets, Environments | None | No changes to branch protection, secrets, or the `consoletrack-revert` environment |
| Expiration | Nov 30, 2026 | Covers both deadlines; renew deliberately after |

Branch protection on `main` requires a pull request with your approval and passing CI, so the agent can propose but never merge. The token is injected into the devcontainer as an environment variable, and the agent hook still blocks mutating `kubectl`, `helm`, and `terraform` commands. The backend's runtime token is a second fine-grained PAT (or a GitHub App later) with Actions write only, used to dispatch `revert.yaml`, plus pull requests write in Phase 2 for Codify.

## Workflow and data model

Changes and incidents are separate records that link to each other: a change can exist with no incident (silent drift), an incident can have no change (an OOM), and the valuable case is an incident linked to the change that caused it. Every agent tool call is stored as evidence.

**Change flow (silent drift).**

1. A developer edits a project resource in the console. CloudTrail records it, the EventBridge rule matches because the caller isn't the Terraform or CI role, and the event lands in SQS, typically within a few minutes.
2. The worker dedupes on the CloudTrail event ID, opens a change in `detected`, and looks up the resource's Terraform address in the demo-app root's state.
3. The agent uses `aws-mcp` to read the current resource and related CloudTrail events, then writes a summary (who, what, when, console or CLI, source IP), a risk rating, and options. The change moves to `awaiting_review`.
4. The reviewer picks Revert, Codify (Phase 2), or Accept with a note. Revert dispatches the gated GitHub workflow; the change moves to `reverting` and then `reverted` when the run succeeds.
5. Every outcome closes the change with a decision, who made it, and a reason. That record is the documentation that was missing.

**What counts as out-of-band.** Detection keys on who made the call, not which tool, so the console is only one source. The change record's `via` field shows where it came from.

| Source | How it's recognized in CloudTrail | Treated as |
| --- | --- | --- |
| AWS console | Console user agent, `sessionCredentialFromConsole` set | Out-of-band change, `via = console` |
| AWS CLI and CloudShell | `aws-cli/...` user agent | Out-of-band change, `via = cli` |
| SDK scripts and other tools | SDK user agents such as `Boto3/...` | Out-of-band change, `via = sdk` |
| Terraform run from someone's laptop | Terraform user agent, caller isn't a pipeline role | Out-of-band, labeled "Terraform outside the pipeline": the code may match, but nobody reviewed the apply |
| Pipeline Terraform (plan, apply, revert roles) | Assumed-role session named `gh-<run id>`, assumable only through GitHub OIDC | Excluded |
| AWS services acting on your behalf (Auto Scaling, EKS, service-linked roles) | `invokedBy` set to an AWS service | Excluded |

The blind spot to close is someone running CLI commands with the pipeline's own credentials. The pipeline roles trust only GitHub OIDC, so no person can assume them, and the EventBridge rule excludes them by session-name prefix rather than by role ARN alone. Any pipeline-role call without a `gh-` session name is flagged high risk. The product name keeps "console" as shorthand for every out-of-band path.

## Intake health and canary

ConsoleTrack must be able to tell "nothing changed" apart from "intake is broken," because a drift detector that fails silently gives false confidence. Every page shows an intake indicator, backed by a health endpoint, a canary, and Prometheus metrics.

| State | Indicator | Triggered by |
| --- | --- | --- |
| Live | Green dot | Worker is polling, the oldest queued message is under 10 min old, and the canary arrived within the last 35 min |
| Delayed | Amber | Oldest message older than 10 min (beyond CloudTrail's normal delivery lag), or canary 35 to 45 min late |
| Down | Red | Worker can't reach SQS, messages in the dead-letter queue, the trail isn't logging, or no canary for over 45 min |
| Replay | Gray, "Replay mode" | `make demo-replay` on kind; the events are recorded fixtures |

**Canary.** A Kubernetes CronJob in the `consoletrack` namespace runs every 15 minutes under a dedicated `ct-canary` Pod Identity role whose only permission is `ec2:CreateTags` on one empty security group, `ct-canary-sg-<suffix>` (no rules, attached to nothing). It sets the tag `ct:canary` to the current time. That write travels through CloudTrail, EventBridge, and SQS exactly like a real change. The worker recognizes the `ct-canary` principal, records when it arrived, and never opens a change record for it. The security group lives in `terraform/consoletrack/us-east-1` with `ignore_changes` on that tag, so the canary is never reported as drift. Without the canary, a quiet account looks the same as a broken pipeline; with it, intake is proven end to end every 15 minutes.

**Health endpoint.** `GET /api/v1/intake/health` returns the state, the watched region, the last event time, the last canary time, the queue backlog and oldest message age, the dead-letter count, and whether the trail is logging. The worker reads the queue numbers with `sqs:GetQueueAttributes` and the trail with `cloudtrail:GetTrailStatus`.

**Metrics and Grafana.** The backend's `/metrics` endpoint exports `consoletrack_intake_last_event_timestamp_seconds`, `consoletrack_canary_last_seen_timestamp_seconds`, `consoletrack_sqs_oldest_message_age_seconds`, `consoletrack_dlq_messages`, and `consoletrack_intake_state`. The `consoletrack` chart ships a ServiceMonitor, an "Intake health" Grafana dashboard as a ConfigMap picked up by the Grafana sidecar, and a `ConsoleTrackCanaryMissing` alert rule (no canary for 45 minutes). The alert routes through Alertmanager like any other, so a broken intake also shows up as an incident in ConsoleTrack; if the backend itself is down, Grafana still shows the gap.

**In the UI.** The header indicator is a button, not just a label. Hovering or focusing it shows a one-line explanation ("Shows whether AWS change events are reaching ConsoleTrack"); clicking it opens a small panel with the state, the health details above, a key to the four states, and a link to the Grafana dashboard.

**Incident flow (symptom first).**

1. Alertmanager posts a firing alert; the backend opens an incident in `open`.
2. The worker gathers pod evidence through `k8s-mcp`. If logs show an AWS error (`AccessDenied`, `ExpiredToken`, `KMS` failures), it uses `aws-mcp` to list changes in the last 24 hours to the IAM role, policy, bucket, or key the workload uses, and links any matching change record.
3. If the cause is in the cluster, the proposed fix is a cluster patch. If it's a linked change, the proposed fix is that change's Revert.
4. After approval and the `resolved` webhook, the incident closes with time-to-resolve and the linked change.

**Tables (Postgres, managed with Alembic migrations).**

| Table | Key columns | Notes |
| --- | --- | --- |
| changes | id, cloudtrail\_event\_id, event\_time, principal\_arn, session\_name, via (console, cli, sdk), source\_ip, service, event\_name, resource\_arn, tf\_address, risk, status, decision, decided\_by, reason | One row per out-of-band write; the change log |
| incidents | id, fingerprint, alertname, severity, namespace, workload, status, opened\_at, resolved\_at | One row per alert episode |
| incident\_changes | incident\_id, change\_id, confidence | Links symptoms to causes |
| agent\_runs | id, subject\_type (change or incident), subject\_id, model, started\_at, finished\_at, input\_tokens, output\_tokens, outcome | Cost and latency analytics |
| evidence | id, agent\_run\_id, tool\_name, args\_json, result\_excerpt, created\_at | Results truncated and redacted before storage |
| analyses | id, subject\_type, subject\_id, summary, root\_cause, risk, confidence, postmortem\_md | Postmortem editable by humans |
| remediations | id, subject\_type, subject\_id, kind (cluster\_patch, rollback, revert\_workflow, codify\_pr), payload\_json, status, decided\_by, external\_url, applied\_at, error | `external_url` links the workflow run or pull request |

**Environments.** `DATABASE_URL` selects the database: Postgres in Docker Compose locally, a Testcontainers Postgres in integration tests, and CloudNativePG in EKS. Unit tests use a fake repository, not SQLite, so SQL behavior is only tested against real Postgres.

## API contract

`openapi.yaml` is written first, from the dashboard's screens, and is the source of truth: the frontend client is generated from it and a CI test fails if FastAPI's generated schema drifts from it.

| Method | Path | Used by | Purpose |
| --- | --- | --- | --- |
| POST | /api/v1/alerts/webhook | Alertmanager | Ingest firing and resolved alerts |
| GET | /api/v1/changes | Change log | Filter by status, principal, service, risk, date; paginated |
| GET | /api/v1/changes/{id} | Change detail | Change with analysis, linked incidents, remediation |
| POST | /api/v1/changes/{id}/revert | Revert button | Dispatch the gated revert workflow; returns the run URL |
| POST | /api/v1/changes/{id}/accept | Accept button | Close with a required note |
| POST | /api/v1/changes/{id}/codify | Codify button (Phase 2) | Open a pull request adopting the change |
| GET | /api/v1/incidents | Incident list | Filter by status, severity, namespace; paginated |
| GET | /api/v1/incidents/{id} | Incident detail | Incident with analysis, linked changes, remediation |
| GET | /api/v1/{subject}/{id}/evidence | Evidence tab | Tool calls the agent made, for a change or an incident |
| POST | /api/v1/incidents/{id}/triage | Re-run button | New agent run, optional model override |
| POST | /api/v1/incidents/{id}/remediation/approve | Approve button | Apply the proposed cluster fix, or trigger the linked change's revert |
| POST | /api/v1/incidents/{id}/remediation/reject | Reject button | Close with a reason |
| PUT | /api/v1/incidents/{id}/postmortem | Postmortem editor | Save edited markdown |
| POST | /api/v1/integrations/github/workflow-run | GitHub webhook | Update revert status when the run finishes |
| GET | /api/v1/intake/health | Header indicator and panel | Intake state, last event and canary times, queue age, dead-letter count, trail status |
| GET | /api/v1/stats | Analytics strip (Phase 2) | Changes by principal, time to document, reverted vs codified |
| GET | /metrics | Prometheus | Intake and agent metrics for Grafana and alert rules |
| GET | /healthz, /readyz | Kubernetes probes | Liveness and readiness (DB check) |

**Contract workflow.** Edit `openapi.yaml` → `openapi-typescript` regenerates `frontend/src/api/schema.d.ts` → backend Pydantic models are hand-written to match → `tests/contract/test_openapi.py` compares `app.openapi()` with the file. Auth for the MVP is two fixed accounts, admin and a read-only viewer, each issued a session cookie, with every write endpoint requiring admin; the webhook uses its own bearer token.

## Triage agent and guardrails

The agent can only read, and that is enforced by IAM and Kubernetes RBAC on two separate MCP servers, not by the prompt. Anything that changes the cluster or AWS goes through an approval and runs under a different identity.

**Runtime.** A worker in the backend calls the Claude Messages API with the Anthropic Python SDK, connects to the `k8s-mcp` and `aws-mcp` servers as an MCP client (FastMCP, streamable HTTP inside the cluster), and offers its tools to the model. The run ends when the model calls a `submit_analysis` tool whose JSON schema is the structured result, so no free-text parsing is needed. Limits per run: 12 tool calls, 120 seconds, a token budget, and one automatic retry.

**MCP tools (k8s-mcp, read-only).**

| Tool | Returns | Typical use |
| --- | --- | --- |
| get\_workload(namespace, kind, name) | Image, resources, probes, replicas, env names (values redacted) | Spot a 128Mi limit or a bad probe path |
| get\_pods(namespace, selector) | Phase, restarts, last termination reason and exit code | Confirm OOMKilled vs Error |
| get\_pod\_logs(namespace, pod, container, previous, tail) | Last N lines, capped at 200 | Read the crash message |
| get\_events(namespace, involved\_object) | Recent warning events | ImagePullBackOff, FailedScheduling, probe failures |
| get\_rollout\_history(namespace, deployment) | ReplicaSets with revision, image, created time | What changed just before the alert |
| query\_prometheus(promql, minutes) | Instant or short range result | Memory trend before an OOM |

**MCP tools (aws-mcp, read-only).**

| Tool | Returns | Typical use |
| --- | --- | --- |
| get\_resource\_changes(resource\_arn, hours) | CloudTrail writes to that resource: who, when, console or CLI, source IP, request parameters | Find the change behind a failure |
| get\_principal\_activity(principal\_arn, hours) | Other writes by the same caller in the window | Spot related changes made in one session |
| get\_iam\_role(role\_name) | Trust policy, inline and attached policies (watched IAM paths only) | See what was removed or loosened |
| simulate\_access(role\_name, actions, resources) | Allowed or denied, with the deciding statement | Confirm the cause of an `AccessDenied` |
| get\_security\_group(group\_id) | Inbound and outbound rules | Judge exposure of an opened port |
| lookup\_terraform\_address(resource\_arn) | Terraform address and recorded attributes from the demo-app root's state | Show what code expects versus what exists |

CloudTrail has two quirks the agent is told about. Events can take several minutes to appear, so when pod logs show an AWS error but no change is found yet, the worker re-runs the lookup after a delay instead of concluding "no change." `LookupEvents` also has a low per-account rate limit, so `aws-mcp` caches results per run.

**Guardrails.**

- `k8s-mcp` runs with its own ServiceAccount: `get`/`list`/`watch` on pods, pods/log, events, deployments, replicasets, statefulsets, limited to an allowlist of demo namespaces. No access to Secrets or ConfigMaps.
- Pod logs and events are untrusted input. The system prompt tells the model to treat them as data, and even a successful prompt injection can only produce a proposal.
- The backend validates every proposed remediation against an allowlist of fields and kinds before showing it, and the backend's apply ServiceAccount can only `patch` deployments in the same namespaces.
- Evidence results are redacted (tokens, keys, connection strings) before they are stored or sent to the model.
- The system prompt (versioned in `backend/app/agents/prompts/`) asks the agent to flag database migrations and stateful workloads and not to propose a rollback for them.

Cloud-specific guardrails: CloudTrail fields such as request parameters and user agents are treated as untrusted input like pod logs; `aws-mcp` strips session tokens and access key IDs from results; and the agent never proposes IAM changes broader than what the Terraform code already grants, so Revert can only narrow permissions back to the reviewed state.

**Stretch: commit tracing (P2).** Add `get_commit_diff(repo, base, head)` and `get_file_lines(repo, path, line)` backed by a fine-grained read-only GitHub token. Deployments carry the repo and SHA as annotations, so the agent can map the previous and current ReplicaSet images to a compare range and correlate a stack trace with the diff.

## Remediation modes

Each alert type maps to a remediation policy. The MVP ships every policy as `manual`; a scenario moves to `auto` only after it has been tested, through a reviewed pull request. Even in `auto`, the agent never touches the cluster: the backend's policy gate approves on the human's behalf and applies through the same `remediator` ServiceAccount.

```yaml
# helm/consoletrack/values.yaml
remediation:
  autoEnabled: false          # global kill switch, also toggled from the dashboard
  policies:
    - name: oom-memory-bump
      match: {alertname: KubePodOOMKilled, namespace: demo}
      mode: auto              # manual | auto
      allowedChanges: [resources.limits.memory, resources.requests.memory]
      bounds: {maxMultiplier: 2, ceiling: 1Gi}
      minConfidence: high
      rateLimit: {perWorkload: 1, window: 1h}
      verify: {waitForResolved: 10m, onFailure: rollback}
    - name: default
      match: {}
      mode: manual
```

**What `auto` adds on top of the manual flow.**

1. The proposal must pass the same allowlist plus the policy's own narrower `allowedChanges` and `bounds`.
2. The backend runs a server-side dry run of the patch before applying it.
3. Rate limit per workload, and the global kill switch, are checked first.
4. After applying, the backend waits for the `resolved` webhook. If it doesn't arrive in time, it rolls back and reopens the incident for a human.
5. The remediation row records `decided_by = policy:oom-memory-bump`, and the dashboard (plus Slack in P1) shows it as auto-applied.

**Graduating a scenario to `auto`.** The scenario has a fault toggle in the `demo-app` chart, has passed `make eval` five runs in a row with the correct root cause and patch, and the policy change is merged by PR with the eval output attached. This mirrors the headless auto-response pattern from the 04-devops module, but opted in per alert rather than globally.

**Headless Claude Code runner (P2).** An alternative agent backend runs `claude -p` in a Kubernetes Job with only the `k8s-mcp` tools allowed and JSON output. It must return the same `submit_analysis` schema, so it passes through the same policy gate and needs no extra cluster permissions.

**AWS-side changes stay manual.** Revert dispatches `revert.yaml` with the change ID. The run waits in the `consoletrack-revert` GitHub environment for a named reviewer, assumes the revert role through OIDC, runs `terraform plan` on `terraform/demo-app/us-east-1`, posts the plan to the run summary, and applies exactly that plan. Codify (Phase 2) opens a pull request with the agent's HCL, and CI must show a `terraform plan` with no changes against the live resource before it can merge, which proves the code now matches reality. A later version could allow auto-revert for narrow, high-risk patterns, such as SSH opened to 0.0.0.0/0, under the same graduation rules as cluster scenarios.

## Agent extension pack

The pack is cheap because its centerpiece already exists: the same `k8s-mcp` server the runtime agent uses is wired into Claude Code for development against a local kind cluster. The rest is files that also document the AI workflow for criterion 2.

| Required piece | Deliverable | Location |
| --- | --- | --- |
| Project instructions | Stack, commands, conventions, "contract first", "never apply to a cluster" | `AGENTS.md`, read by Claude Code directly; the repo has no `CLAUDE.md`, which would stop it loading |
| Reusable workflow | `add-endpoint` skill: edit openapi.yaml → regenerate client → write failing test → implement → run contract test | `agent-capabilities/add-endpoint/SKILL.md` |
| Reusable workflow | `break-demo` skill: apply one of the fault scenarios to kind and check an incident appears | `agent-capabilities/break-demo/SKILL.md` |
| Subagent / specialist | `k8s-manifest-reviewer`: reviews Helm and RBAC diffs for least privilege, limits, probes | `custom-agent/k8s-manifest-reviewer.md` |
| MCP server | `k8s-mcp` and `aws-mcp`, read-only tools, plus a `.mcp.json` for local use against kind and a sandbox AWS profile | `mcp-server/` |
| Hook / guardrail | PreToolUse guard enforcing every `AGENTS.md` hard rule a tool call can break: commits and pushes to `main`, force pushes, Terraform/kubectl/helm/AWS writes, `.env` access, edits under `.github/workflows/`, and `CLAUDE.md` files. `.claude/settings.json` deny rules back it up. PostToolUse runs ruff, eslint, or `terraform fmt` on edited files | `agent-hooks/` |
| Permission notes | Who can do what: dev-time agent, runtime agent, backend apply SA, CI deploy role | `docs/permissions.md` |
| Overview | How the pieces fit and how to install them | `docs/agent-extension-pack.md` |

**Development setup.** Claude Code runs in your existing rootless dev-agent container, with a kind cluster on the host. Moving that setup to Podman is optional and off the critical path; nothing in the project depends on the container engine. The PreToolUse hook makes the current practice of handing mutating commands back to a human an enforced rule rather than a habit. `docs/ai-workflow.md` keeps a short log per feature: the prompt or task given, which context files were loaded, what was reviewed by hand, and how it was verified.

## Testing, CI/CD, and deployment

CI never calls the real Claude API or needs a running EKS cluster: the agent is tested with a fake model client, and deploys are skipped automatically when the cluster is down.

**Test layers.**

| Layer | Tooling | Covers | Command |
| --- | --- | --- | --- |
| Frontend unit | Vitest, React Testing Library, MSW | API client wrapper, incident list filters, approve/reject flow, postmortem editor | `make test-frontend` |
| Backend unit | pytest | Webhook parsing and dedupe, state machine, remediation allowlist, policy gate (bounds, rate limit, kill switch, verify-or-rollback), redaction, agent loop with a fake model | `make test-backend` |
| Contract | pytest | FastAPI schema matches `openapi.yaml` | `make test-contract` |
| Integration | pytest + Testcontainers Postgres | Webhook → incident → fake agent → approve → patch sent to a fake K8s client, all over HTTP | `make test-integration` |
| Cluster integration | pytest + kind in Actions | `k8s-mcp` tools against a real API server with the real RBAC | `make test-k8s` |
| Agent eval (manual) | script against kind | Three fault scenarios with the real model; outcome logged in `docs/evals.md` | `make eval` |

Integration tests live in `backend/tests/integration/` behind a pytest marker so the unit suite stays fast.

`aws-mcp` tools are unit-tested against moto. The change-flow integration test feeds recorded CloudTrail events into a local SQS-compatible queue (ElasticMQ container) and checks that a change record, Terraform address, and analysis come out, with no AWS account needed.

**Local run.** `docker compose up` starts Postgres, backend, worker, `k8s-mcp`, and the frontend. `make demo-local` adds a kind cluster with kube-prometheus-stack and the `demo-app` chart, with Alertmanager pointed at the Compose backend.

**GitHub Actions.**

1. `ci.yaml` on every PR and push: ruff, eslint, all automated test layers, and arm64 image builds on native arm64 runners. An amd64 variant is built only so reviewers on x86 machines can run Compose locally; the cluster runs arm64 only.
2. `deploy.yaml` after CI passes on `main`: deploy role through GitHub OIDC → push arm64 images to GHCR tagged with the commit SHA → if the cluster exists, `helm upgrade --atomic` → smoke test `/readyz` through a port-forward from the runner. If the cluster is down the job logs a skip and stays green.
3. `terraform.yaml` for Terraform. On pull requests that touch `terraform/**`, the plan role (GitHub OIDC) runs `fmt`, `validate`, tflint, and `terraform plan` for each changed root and posts the plans as a PR comment. Because the roots are siblings, a change to `demo-app/` plans only that root. After merge to `main`, the apply role applies in the `consoletrack-apply` environment once you approve the run. Manual dispatch remains for `destroy`.

A fourth workflow, `revert.yaml`, is dispatched only by the backend. It runs in the `consoletrack-revert` environment, which requires reviewer approval, and plans then applies the `terraform/demo-app/us-east-1` root.

**Which account each workflow deploys to.** Each account has its own directory, `terraform/<account>/...`, and its own GitHub environments (`<account>-plan`, `<account>-apply`, `<account>-revert`). Each environment stores that account's role ARN as an environment secret. The ARN isn't a credential, since OIDC stores no keys, but keeping it secret means the public repo and the workflow logs never show the account ID. Terraform never hardcodes account IDs; it reads them from aws\_caller\_identity. After authenticating, each job masks the bare account ID (from aws sts get-caller-identity), so logs hide it inside every ARN in the plan, not only the full role ARN. The plan comment on PRs replaces the account ID with a placeholder before it is posted, because masking doesn't apply to comments. This is best-effort hygiene, not a security boundary. The recorded CloudTrail fixtures for demo-replay use the placeholder 123456789012. `terraform.yaml` builds a matrix from the account directories a change touches, and each matrix job runs with its account's environments. For the course there is one entry, `consoletrack`, and `terraform/demo-app/us-east-1` uses the hub's roles: `ct-gh-plan-<suffix>`, `ct-gh-apply-<suffix>`, and `ct-gh-revert-<suffix>` (plus `ct-gh-deploy-<suffix>` for app deploys). Each role trusts only this repo, and the apply and revert roles trust only their environment. When `demo-app` becomes its own account, a new `terraform/demo-app/global/` root creates its GitHub OIDC provider and its `demo-app-gh-plan`, `demo-app-gh-apply`, and `demo-app-gh-revert` roles. The demo-app environments then get their own values, and it becomes a second matrix entry with no workflow changes. Each account trusting GitHub directly is preferred over going through the hub. A compromised hub role can't reach the spoke, and each account's CloudTrail shows which workflow run made each change.

**Proof of deployment.** The README holds the URL and the dates it is live; keeping the cluster up through the one-week peer-review window costs about $27. Screenshots of the dashboard, Grafana, and a green deploy run stay valid after teardown. A short demo video on YouTube (change detected → reverted; alert fires → incident → approve → resolved) is optional, as time allows; it is not a course requirement.

## Repository layout

The layout keeps every path the course lists at the root and adds `terraform/` (by AWS account, then global or region, one root per folder) and `helm/` (one chart per app). The name `demo-app` is used for both halves of the demo application: `helm/demo-app` for its Kubernetes side and `terraform/demo-app/us-east-1` for its AWS side.

```text
README.md                 # problem, architecture, reviewer quick start, screenshots
product-spec.md           # this document, exported
AGENTS.md                 # the only agent instructions file; no CLAUDE.md
openapi.yaml
docker-compose.yaml
.env.example              # every setting a reviewer changes, with comments; the only config file
Makefile                  # preflight, up, deploy, demo-*, revert, down, down-all, verify-clean
frontend/                 # React + Vite + TypeScript; src/api/ is the only place that calls the backend
backend/
  app/api/                # routers
  app/services/           # change and incident state machines, remediation validator, policy gate, apply
  app/intake/             # SQS consumer, CloudTrail event parsing, canary heartbeat
  app/agents/             # agent loop, prompts/, structured output schema
  app/db/                 # SQLAlchemy models, Alembic migrations
  tests/unit/  tests/integration/  tests/contract/
mcp-server/k8s/          # k8s-mcp (FastMCP), Dockerfile
mcp-server/aws/          # aws-mcp (FastMCP, boto3), Dockerfile, IAM policy notes
.mcp.json
terraform/
  consoletrack/           # the ConsoleTrack hub account
    global/               # iam.tf (GitHub OIDC, CI roles, boundary, demo developer role), route53.tf, budgets.tf (optional)
    us-east-1/            # vpc.tf, nat.tf, eks.tf, pod-identity.tf, intake.tf, acm.tf, addons.tf
  demo-app/               # named for its future spoke account; deployed into consoletrack for now
    us-east-1/            # demo app IAM, S3, security group, Pod Identity association: the drift target
helm/
  consoletrack/           # backend, worker, frontend, MCP servers, canary CronJob, Gateway API routes, RBAC, alert rules, dashboards, policies
  demo-app/               # the demo workload; faults (oom, bad-image, bad-probe) are values toggles
  addons/                 # values for third-party charts (kube-prometheus-stack, cloudnative-pg, LBC, ExternalDNS)
scripts/                  # bootstrap-state.sh, preflight.sh, teardown-verify.sh, wait-for.sh
  scenarios/              # drift.sh, identity.sh: cloud scenarios run as the demo developer role
.github/workflows/        # ci.yaml, deploy.yaml, terraform.yaml, revert.yaml
.devcontainer/            # pinned tool versions for reviewers who prefer a container
agent-capabilities/  agent-hooks/  custom-agent/
docs/                     # architecture, ai-workflow, agent-extension-pack, permissions, evals, troubleshooting, mockups/
security/  ops/           # placeholders until P2 work
```

## Reviewer deployment

A peer reviewer with their own AWS account and Anthropic API key stands up the whole environment with five `make` targets in about 25 minutes, and a final check proves nothing billable was left behind. Nothing depends on the author's accounts, domain, or registry credentials.

**Prerequisites.**

| Need | Detail |
| --- | --- |
| AWS account | Admin access, ideally a sandbox account; a CLI profile configured |
| Anthropic API key | With a monthly spend limit set; a full demo uses a handful of triage runs |
| Tools | Terraform 1.11+ (S3 native state locking), AWS CLI v2, kubectl, Helm 3, Docker or Podman, make, jq; or open `.devcontainer/` with all of them pinned |
| Budget | About $0.19/hour while up; the README states this before any command |

**Steps (README "Quick start" mirrors this exactly).**

1. `cp .env.example .env` and set `AWS_PROFILE`, `AWS_REGION`, `ANTHROPIC_API_KEY`. `ALLOWED_CIDRS` defaults to the reviewer's current public IP. Any variable already set in the shell overrides `.env`, so `ANTHROPIC_API_KEY` can stay empty in the file and come from a keychain or password manager instead (for example `op run --env-file=.env -- make up`). That keeps the key out of the repo folder, which the devcontainer mounts.
2. `make preflight` checks tool versions, the AWS identity, Spot vCPU quota in the region, that the Kubernetes version is in standard support there, that the API key works, and that no resources with the same name exist.
3. `make up` creates the state bucket, the cluster, add-ons, and the app from the public GHCR images, then prints the dashboard URL and a generated admin password. It is safe to rerun.
4. `make demo` applies the three fault scenarios one at a time, waits for each incident to reach `awaiting_review`, and prints its link.
5. `make down` removes the Gateway and Helm releases first, then destroys `terraform/demo-app/us-east-1` and `terraform/consoletrack/us-east-1`, leaving `terraform/consoletrack/global` (roles, zone, optional budget) and the state bucket for next time. `make down-all` also destroys `global` and the state bucket for a fully clean account. `make verify-clean` lists any project-tagged load balancers, ENIs, EBS volumes, or public IPs still present and exits non-zero if it finds one.

**No-AWS option.** `make local` runs the same stack on kind with Docker Compose, covering every criterion except cloud deployment, for which the README has the live URL during review and screenshots of the EKS deployment. `make demo-replay` then fills in the cloud side without an AWS account: it replays the recorded CloudTrail events used by the change-flow integration tests into a local SQS-compatible queue (ElasticMQ), so the change log, change detail, agent analysis, and linked incident all appear as they would on EKS. The agent runs for real against the Claude API; its AWS lookups are answered from the same recorded fixtures through a `fixtures` mode of `aws-mcp`. Revert and Codify are disabled in this mode and labeled "AWS required" in the UI, and the cluster-side faults (OOM, bad image, bad probe) work normally on kind.

| Works on kind | Needs AWS |
| --- | --- |
| Full UI, API, database, and agent loop | Live CloudTrail intake through EventBridge |
| Cluster faults with real alerts and cluster patches | Live `aws-mcp` lookups and IAM simulation |
| Change flow from replayed CloudTrail events | Revert workflow and Codify pull requests |
| All automated tests, including integration | EKS deployment and Pod Identity |

**What makes this reliable.**

- Every AWS name starts with `ct-` and ends with a random suffix, and every resource carries `project` and `owner` tags, so two reviewers in one account don't collide and cleanup can find everything.
- No hardcoded account IDs, ARNs, or domains. The dashboard opens with `make open` (a port-forward) by default; with `EXPOSURE=alb` it is served on an ALB DNS name restricted to `ALLOWED_CIDRS`. HTTPS with a custom domain is an optional variable.
- The API key and admin password are written to Kubernetes Secrets by a script, not passed through Terraform, so they never land in state.
- Spot capacity errors fall back to on-demand via `USE_SPOT=false`, documented in `docs/troubleshooting.md` with the other known failure modes (quota, slow ALB deletion).
- Before submitting, the author runs the full guide from a fresh clone in a clean AWS account and fixes every step that needed knowledge not written down.

Reviewers who don't set up GitHub OIDC on their fork still get a working Revert: the dashboard shows the exact command, `make revert CHANGE=<id>`, which plans and applies the demo-app root from their machine after a confirmation prompt. `make demo-drift` and `make demo-identity` assume the demo "developer" role, so the changes appear in CloudTrail as an outside principal just as a real console edit would.

## Rubric mapping

The MVP targets 28 of 30 points; the 2 left are the security criterion, which stays a stretch.

| # | Criterion | Max | Evidence a reviewer will find |
| --- | --- | --- | --- |
| 1 | Problem description | 2 | README opening: problem, functionality, expected demo behavior |
| 2 | AI-assisted workflow | 2 | `docs/ai-workflow.md` log, AGENTS.md, skills, hook, review and verification notes |
| 3 | Technologies and architecture | 2 | README architecture diagram and a table of components and their roles |
| 4 | Frontend | 3 | Centralized `src/api/` client generated from OpenAPI; Vitest suite; `make test-frontend` |
| 5 | API contract | 2 | `openapi.yaml` written first; contract test in CI |
| 6 | Backend | 3 | Layered FastAPI app; unit tests; follows the spec |
| 7 | Database | 2 | Alembic migrations; Compose, Testcontainers, and CloudNativePG configs; documented |
| 8 | Containerization | 2 | `docker compose up` runs the full app; images for all four services |
| 9 | Integration testing | 2 | Separate `tests/integration/` with Testcontainers plus kind-based tests, documented |
| 10 | Deployment | 2 | EKS via Terraform modules and Helm; URL during review, screenshots, optional demo video; reviewers can deploy it themselves |
| 11 | CI/CD | 2 | `ci.yaml` tests; `deploy.yaml` deploys on green `main` |
| 12 | Agent extension pack | 2 | All six required pieces plus overview doc |
| 13 | Security, audit, DevOps | 2 | P2: RBAC design and permissions doc give a start; scans added if time allows |
| 14 | Reproducibility | 2 | Reviewer quick start with preflight, one-command up, demo, down, and verify-clean; dry-run in a clean account |

If time runs short, criterion 13's cheapest point is one deterministic scan committed to `security/` (for example Trivy on the images and Checkov on `terraform/`), which is about two hours of work.

## Plan

Phase 1 is built by Oct 23 for the Monday, Oct 26 submission (6 pm local), with a go/no-go check on Oct 22: if the cloud scenarios and revert workflow aren't working by then, we keep building and submit on Nov 16 rather than cut the drift features. Phase 2 runs to Nov 16 either way.

| Workstream | Dates |
| --- | --- |
| Contract + skeleton | Oct 13 – Oct 14 |
| Backend + database | Oct 14 – Oct 16 |
| Change intake + aws-mcp | Oct 15 – Oct 17 |
| Agent loop, both MCPs | Oct 16 – Oct 19 |
| EKS + deploy pipeline | Oct 17 – Oct 18 |
| Frontend + change log | Oct 18 – Oct 21 |
| Cloud scenarios + revert | Oct 20 – Oct 22 |
| **Go/no-go check** | **Oct 22** |
| Tests, evals, extension pack | Oct 21 – Oct 23 |
| Docs, clean-account run | Oct 24 – Oct 25 |
| **Submit (Zoomcamp)** | **Oct 26, 6 pm** |
| Phase 2: codify PRs | Oct 27 – Nov 4 |
| Phase 2: more scenarios | Nov 3 – Nov 8 |
| Phase 2: Slack, analytics | Nov 9 – Nov 12 |
| **Fallback deadline** | **Nov 16** |

The change intake and `aws-mcp` start on day 3 because they are the riskiest new pieces and can be tested against a sandbox account before the cluster exists. The deploy pipeline still lands Oct 18, so later work only needs merges. Each phase ends with a blog-ready milestone: the Oct 26 demo, and the Nov 16 codify-and-scenarios release.

## Open decisions and risks

**Decisions to confirm.**

- [ ] Submission deadline: Monday, Oct 26, 6 pm local time (decided).
- [ ] Region: fixed to us-east-1 for IAM events (decided).
- [ ] Frontend: React + Vite SPA served by nginx (chosen for simplicity) vs Next.js as in the earlier sketch.
- [ ] Slack: moved to Phase 2 (decided).
- [ ] Public repo, which peer review needs and which also gives free arm64 Actions runners.

* [ ] Project name: ConsoleTrack (decided).
* [ ] Whether to submit Oct 26 or Nov 16, decided at the Oct 22 check.
* [ ] Which AWS services the EventBridge rule watches in Phase 1 (proposed: IAM, EC2 security groups, S3 bucket policy).
* [ ] Which change types count as high risk by default (proposed: IAM policy or trust changes, security group ingress from 0.0.0.0/0, bucket policy or public access changes).

**Risks.**

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| CloudTrail delivery delay makes the live demo slow or flaky | High | If the optional demo video is made, speed up the CloudTrail wait in editing with an on-screen note of the real elapsed time; the worker retries lookups; `make demo-*` waits for the change record before moving on |
| Revert workflow or apply role ends up broader than intended | Medium | Permissions boundary, ARNs scoped by prefix and IAM path (`ct-` for ConsoleTrack, `demo-app-` for the watched workload), demo-app root kept tiny; `make audit-iam` runs IAM Access Analyzer policy validation on every role |
| Agent links the wrong change to an incident | Medium | Link confidence stored and shown; IAM simulation must confirm the denial before a link is marked high confidence |
| The single NAT instance fails or is replaced mid-demo | Low | fck-nat runs in an Auto Scaling group of one and recovers on its own; `make up` checks egress to the Claude and GitHub APIs before the demo starts |
| Spot reclaim during the review window or a recording | Medium | Node group allows t4g.medium and t4g.large; switch to on-demand for demo day |
| Memory pressure from the Prometheus stack on 8 GiB total | Medium | Short retention, exporters trimmed, resource requests set; add a third node if pods go Pending |
| `terraform destroy` hangs on load balancer or ENI cleanup | Medium | Destroy script removes Gateway and Helm releases first |
| Schedule slip | High | Oct 22 go/no-go with Nov 16 as the planned fallback, instead of cutting the differentiator |
