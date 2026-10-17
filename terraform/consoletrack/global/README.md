# terraform/consoletrack/global

Account-wide, long-lived root in the ConsoleTrack hub account. It survives `make down`.

| Resource | When | Name |
|---|---|---|
| Permissions boundary | always | `ct-boundary-<suffix>` under `/ct/` |
| Demo developer role | always | `ct-demo-developer-<suffix>` under `/ct/` |
| GitHub OIDC provider | `ENABLE_GITHUB_OIDC=true` | `token.actions.githubusercontent.com` |
| CI roles | `ENABLE_GITHUB_OIDC=true` | `ct-gh-{plan,apply,revert,deploy}-<suffix>` under `/ct/` |
| Apply role managed policies | `ENABLE_GITHUB_OIDC=true` | `ct-gh-apply-{read,iam,infra}-<suffix>` under `/ct/` |
| Route 53 public zone (+ NS delegation with `DNS_PARENT=route53`) | `DOMAIN` set | `DOMAIN` |
| Monthly cost budget, alerts at 50/80/100% actual and 100% forecasted | `ENABLE_BUDGET=true` | `ct-budget-<suffix>` |

Every resource is tagged `project=consoletrack`, `owner=<NAME_SUFFIX>`, `managed_by=terraform`.
Spec: `product-spec.md` → AWS design and cost, Identity and access, Custom domain.

## Bootstrap order (once per account, by a human)

This is the one root a human applies locally; it creates the roles CI uses for everything after.

1. `make bootstrap`: creates the state bucket `ct-tfstate-<suffix>` (versioned, SSE-S3, public
   access blocked, TLS only). If `NAME_SUFFIX` is empty it prints a generated one: add it to `.env`.
2. Set the rest of `.env`: `GITHUB_REPO` and `ENABLE_GITHUB_OIDC=true` for CI, `BUDGET_EMAIL` (or
   `ENABLE_BUDGET=false`), `DOMAIN`/`DNS_PARENT` if you want HTTPS, and optionally `OPERATOR_ARN`.
3. `make tf-init ROOT=consoletrack/global && make tf-plan ROOT=consoletrack/global`, and compare the
   plan with the resource list in the PR that last changed this root.
4. `make tf-apply ROOT=consoletrack/global` with an admin session. Terraform shows the plan and asks
   for `yes`; the target refuses to run in CI or without a terminal.
5. `make gh-secrets`: stores each role ARN as its GitHub environment's `AWS_ROLE_ARN` (below). If
   `ENABLE_BUDGET=true`, also add `BUDGET_EMAIL` as a repository secret (`gh secret set BUDGET_EMAIL`,
   which prompts for the value); CI passes it to Terraform as `TF_VAR_budget_email`.
6. If `DOMAIN` is set with `DNS_PARENT=external`, add the NS records (below).

`make domain-zone` applies only the zone (`-target`) and prints the NS records, for setting up a
domain before the rest of the root.

## GitHub environment secrets

Each GitHub environment holds its role's ARN as the secret `AWS_ROLE_ARN`. The ARN isn't a
credential (OIDC stores no keys), but keeping it secret keeps the account ID out of the public repo
and logs. Set all four with:

```sh
make gh-secrets
```

It runs `scripts/gh-env-secrets.sh`, which reads the `github_env_role_arns` output (environment
name → role ARN) and pipes each ARN into `gh secret set` on stdin, so no ARN or account ID is
printed. Run it on your own machine, as yourself: it refuses to run when `gh` is logged in as the
agent's account. It needs `GITHUB_REPO`, an initialized and applied root (`make tf-init
ROOT=consoletrack/global`), and `gh` logged in with permission to set environment secrets. An
environment that doesn't exist yet in the repo is skipped and reported (exit 1), so create the four
environments first. Re-run it after any change to `NAME_SUFFIX`, since the role names include the
suffix. Don't copy ARNs by hand.

| Environment | Role | Trusted `sub` |
|---|---|---|
| `consoletrack-plan` | `ct-gh-plan-<suffix>` | `repo:<GITHUB_REPO>:environment:consoletrack-plan` |
| `consoletrack-apply` | `ct-gh-apply-<suffix>` | `repo:<GITHUB_REPO>:environment:consoletrack-apply` |
| `consoletrack-revert` | `ct-gh-revert-<suffix>` | `repo:<GITHUB_REPO>:environment:consoletrack-revert` |
| `consoletrack-deploy` | `ct-gh-deploy-<suffix>` | `repo:<GITHUB_REPO>:environment:consoletrack-deploy` |

Give `consoletrack-apply` and `consoletrack-revert` required reviewers; that approval is what gates
every apply and revert.

## Session names: the `gh-` convention

Every workflow that assumes one of these roles sets:

```yaml
- uses: aws-actions/configure-aws-credentials@<sha>
  with:
    role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
    role-session-name: gh-${{ github.run_id }}
```

The intake's EventBridge rule (`terraform/consoletrack/us-east-1`) excludes pipeline calls by the
`gh-` session-name prefix, so CI's own changes don't become change records. The roles' trust policies
require `sts:RoleSessionName` to match `gh-*`, so a workflow that forgets it fails to authenticate
instead of flooding the change log. The demo developer role does the opposite: it refuses `gh-*`
session names, so the simulated console changes are always seen.

## What each role can do

All roles live under `/ct/`, allow at most 1-hour sessions, and carry the boundary.

- **Boundary** (`ct-boundary-<suffix>`): the ceiling for every project role. Allows only EC2/VPC,
  EKS, S3, SQS, EventBridge, CloudTrail, CloudWatch Logs, Route 53, ACM, Budgets, STS, and Elastic
  Load Balancing (for the Load Balancer Controller), plus IAM reads and IAM writes under `/ct/` and
  `/demo-app/`. Three narrow additions make the node role's AWS-managed policies work: ECR image
  pulls (the `AmazonEC2ContainerRegistryPullOnly` set), `eks-auth:AssumeRoleForPodIdentity` on
  `cluster/ct-eks-<region>-*` only (the Pod Identity agent), and `ssm:GetParameter` on AWS's
  public `/aws/service/*` parameters (the node group's AMI lookup, also needed by plan and
  apply). No KMS (EKS uses its default envelope encryption) and no Auto Scaling. Denies changes to itself and to any OIDC
  provider, IAM writes outside the project paths, creating a role without this boundary, removing
  the boundary from a role, and `iam:CreateUser`/`iam:CreateAccessKey`.
- **Plan**: refresh-only reads on project resources, read the state bucket, write only `*.tflock`.
- **Apply** (three managed policies under `/ct/`: `read`, `iam`, `infra`, each under IAM's
  6,144-character limit): create, change, and delete EC2 resources tagged `project=consoletrack` and EKS, S3, SQS,
  EventBridge, CloudTrail, Logs, and Budgets resources named `ct-*`/`demo-app-*`; IAM under the
  project paths only with the boundary attached; Route 53 records only under `DOMAIN`. It can't
  touch the state bucket's settings, the boundary, or the OIDC provider, and can't delete zones.
- **Revert**: only what `terraform/demo-app/us-east-1` manages (`demo-app-*` IAM under `/demo-app/`,
  `demo-app-*` buckets, the security group tagged `Name=demo-app-*`, the demo app's Pod Identity
  association on `ct-eks-use1-<suffix>`), and only that root's state object and lock.
- **Deploy**: `eks:DescribeCluster` on `ct-eks-use1-<suffix>`. Kubernetes access comes from an EKS access
  entry in `terraform/consoletrack/us-east-1`.
- **Demo developer**: assumable only by `OPERATOR_ARN` (default: the role or user that runs
  Terraform here). Edits the demo app's IAM policy and the demo security group's rules, nothing else.

Contracts with the other roots: regional resources are named `ct-<component>-<region>-<suffix>`, with
the region code from `local.region_codes` in `providers.tf` (`use1` for us-east-1). So the EKS
cluster is `ct-eks-use1-<suffix>`, the demo security group carries the tag
`Name=demo-app-sg-use1-<suffix>` (EC2 has no condition key for group names), and every
role those roots create sets `permissions_boundary` to this root's `boundary_arn` output.

Because the boundary denies changes to itself and to the OIDC provider, CI's apply role can't change
either. A PR that edits them is applied by a human with `make tf-apply ROOT=consoletrack/global`.
Leave `OPERATOR_ARN` set (as a repository secret for CI) before CI applies this root, or the demo
developer role's trust would switch to the CI role.

## Importing an existing GitHub OIDC provider

An account has at most one provider per URL. If `token.actions.githubusercontent.com` already
exists (from another project), import it instead of creating a second one, before the first apply.
Use the ARN from `aws iam list-open-id-connect-providers`:

```sh
cd terraform/consoletrack/global
terraform import 'module.github_oidc_provider.aws_iam_openid_connect_provider.this[0]' \
  "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com"
```

Terraform then manages it, and a later `make down-all` would delete it: if other projects use it,
remove it from state first with `terraform state rm 'module.github_oidc_provider.aws_iam_openid_connect_provider.this[0]'`.

## Custom domain

With `DOMAIN=consoletrack.example.com` and `DNS_PARENT=external`, the `domain_ns_instructions`
output lists the four records to add at the parent's DNS provider: host `consoletrack`, type `NS`,
one value per name server (for example in Namecheap: Advanced DNS → four NS records with host
`consoletrack`). With `DNS_PARENT=route53`, Terraform adds them to the parent zone in this account.

## Checks without AWS

```sh
make lint-terraform tflint tf-validate tf-test
```

`make tf-test` runs `tests/global.tftest.hcl`: it overrides every data source and module that would
call AWS or GitHub, and checks the trust policies, the policy size limits (the boundary and each
apply policy under 6,144 characters, inline policies under 10,240), that the boundary allows no KMS
or Auto Scaling, and the feature switches.
