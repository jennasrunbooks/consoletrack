# Permissions

Who can do what in ConsoleTrack. Terraform is AWS-only; Helmfile owns every chart, so the
cluster-side write commands to guard against are `kubectl`, `helm`, and `helmfile`. This page starts with the development-time agent (Claude Code
working on this repo). The runtime identities (the triage agent's MCP servers, the backend's
apply ServiceAccount, the CI plan/apply/revert roles) are added with the Terraform and Helm
issues that create them; the spec's "Identity and access" section is the plan. The AWS roles and their policies are listed
in `terraform/consoletrack/global/README.md` (CI roles, boundary) and
`terraform/consoletrack/us-east-1/README.md` (cluster, nodes, Pod Identity).

## Accepted gaps

Deliberate trade-offs, each documented where it is set, so a reviewer can tell them apart from
mistakes:

| Gap | Where | Why it's accepted | How to close it |
| --- | --- | --- | --- |
| `cloudtrail:LookupEvents` on `*` (aws-mcp) | `terraform/consoletrack/us-east-1/pod-identity.tf` | The action has no resource-level permissions | Not possible in IAM; it's a read |
| `ec2:DescribeSecurityGroups` on `*` (aws-mcp) | same | Same: EC2 describe calls are account-wide | Not possible in IAM; it's a read |
| EKS API public endpoint open to `0.0.0.0/0` | `terraform/consoletrack/us-east-1/eks.tf` | GitHub-hosted runners deploy through it, and their address ranges don't fit EKS's 40-CIDR limit. Every request still needs IAM authentication and an access entry; nodes use the private endpoint | Narrow `EKS_PUBLIC_ACCESS_CIDRS` (one variable), or turn public access off with a pull-based deploy (spec, Production hardening step 4) |

The node role has AmazonEC2ContainerRegistryReadOnly attached (the module’s default); the permissions boundary limits it to GetAuthorizationToken, BatchGetImage, and GetDownloadUrlForLayer.

## Development-time agent

Claude Code runs as `jksprattler-agent` in the devcontainer. No single layer is trusted on its
own: each one below stops something, and the outer layers still hold if an inner one is
bypassed.

| Layer | Where it's set | What it stops | What it doesn't |
| --- | --- | --- | --- |
| Devcontainer | `.devcontainer/` | No AWS keys, no kubeconfig, no Docker socket; runs as non-root with `--cap-drop=ALL` and `no-new-privileges`. Any AWS, cluster, or container write fails for lack of access, however it was started. | Network access: the agent can still reach the internet and GitHub with its token. |
| GitHub token | Fine-grained PAT, owned by a human (see the spec's "Development-time access") | Contents and PRs only. No Workflows permission, so pushes that touch `.github/workflows/` are rejected; no Administration, Secrets, or Environments, so no ruleset, secret, or environment changes. | Pushing feature branches and opening PRs, which is the job. |
| `main` ruleset | GitHub repo settings | Direct pushes and merges to `main`: everything needs a PR with your approval and passing CI. A commit made on local `main` can't land. | What's in the PR; that's what review is for. |
| Environments | GitHub environments `consoletrack-apply`, `consoletrack-revert` (with the Terraform and revert workflows) | `terraform apply` and Revert runs wait for a named reviewer, and only those environments' OIDC roles can write to AWS. | Plans, which are read-only. |
| `permissions.allow` | `.claude/settings.json` | Nothing; it removes prompts for routine, safe commands (`make test`, `make lint`, each `make test-*`/`lint-*` target by exact name, `make helm-lint`, `make helm-test`, `terraform fmt`, `helm lint`, read-only `git` and `gh`). No `make` target that touches AWS or a cluster is listed; see "Adding to the rules". | — |
| `permissions.deny` | `.claude/settings.json` | Backup for the guard: reading `.env` and the common `.env.<name>` variants (listed one by one, because deny globs can't exclude `.env.example`, which the agent maintains), editing `.github/workflows/` and `CLAUDE.md`, the Terraform/kubectl/helm write commands, `helmfile apply/sync/destroy/delete/init`, `aws configure`, `aws sso login`, `eksctl`, force pushes, `gh pr merge`, `gh api`, `gh secret`, `gh variable`, `gh repo edit`, `gh workflow run`. See "Guard and deny rules" below for which one answers first. | Commands it can't pattern-match: flags in another order, wrappers, nested shells. The guard covers those. |
| PreToolUse guard | `agent-hooks/guard.py` | Tokenizes each command and file path and blocks everything in `AGENTS.md → Hard rules` that a tool call can break, including chained, wrapped, and nested forms. For `helmfile` it allows only known reads (`lint`, `template`, `diff`, `deps`, `build`, `list`, …) after any global flags, and blocks helm write verbs passed through `--` or `--args`. It also rejects a `*.tf` edit that adds the Terraform `helm` or `kubernetes` provider, which `make lint` (`lint-terraform-providers`) checks again for humans and shell writes. See [agent-extension-pack.md](agent-extension-pack.md#hooks). | See "What the guard can't see" below. |
| PostToolUse lint | `agent-hooks/lint_edited.py` | Lint errors in edited Python, frontend, and Terraform files, reported back in the same turn. | It's a quality check, not a security control. |
| Human review | PR review, `CODEOWNERS` | Anything the layers above miss, including code that would do something harmful once a human runs it. | — |

### Guard and deny rules

What we observed in fresh sessions on Claude Code 2.1.300:

- **Bash.** The guard runs first, and its message is what the agent sees. That held for
  `terraform apply` even though `Bash(terraform apply*)` is also a deny rule.
- **Edit and Write on denied paths** (`.github/workflows/**`, `CLAUDE.md`). There are only
  `Edit(…)` rules: Claude Code applies them to every file-writing tool, including Write, and
  warns at startup about separate `Write(…)` rules. The deny rule is
  checked first. The agent gets the generic `File is in a directory that is denied by your
  permission settings.`, and the guard doesn't run. For `.github/workflows/`, that means the
  agent doesn't see the guard's pointer to `docs/proposed-workflows/`. `AGENTS.md` rule 4
  gives the same instruction.
- **`.env` files.** A `Read` deny rule also stops `Edit` and `Write` on the matching path, so
  the earlier `Read(./.env.*)` glob kept the agent from maintaining `.env.example`. Deny globs
  have no "except", so the rules now list `.env` and the common variants (`.env.local`,
  `.env.*.local`, `.env.production`, `.env.bak`, …) by name. The guard still blocks every
  `.env.*` name except `.env.example`, so an unusual name like `.env.mine` is stopped by the
  guard alone.

- **Helmfile.** The deny rules cover `helmfile <verb>*` and `helmfile * <verb>*` for `apply`,
  `sync`, `destroy`, `delete`, and `init`; the guard answers first, as for Terraform, and also
  catches `test`, `charts`, unknown verbs, and helm passthrough.

- **Allow rules keep `*` at the end only.** Claude Code warns at startup about a wildcard in the
  middle of a command (`uvx ruff@* check *`, `terraform -chdir=* validate`). Those commands
  run through `make` targets instead (`make lint`, `make test`, `make tf-validate`), which
  hold the pinned versions and are allowed by exact name.

The call is blocked either way. The deny rules are kept as a backup in case the hook fails:
for example, if `python3` is missing, the hook times out, or the guard has a parsing gap.

### What the guard can't see

The guard reads the command the agent is about to run. It can't see what that command does
later, so these get past it by design:

- **Commands assembled at runtime**, e.g. `c=terr; ${c}aform apply`, or a value read from a file.
- **Scripts the agent writes and then runs**: `bash deploy.sh`, `python x.py`, `make <target>`,
  `npm run <script>`. The guard checks the `Write` (which isn't blocked unless it targets a
  protected path) and the run, but not the script's contents.
- **Interpreters with inline code**: `python -c "import subprocess; …"`, `node -e`, `perl -e`.
- **Git aliases and config**: an alias defined in git config that expands to `push --force`.
- **Indirect `.env` reads**: a recursive search that happens to include `.env`, or a tool that
  loads it implicitly (Docker Compose reads `.env` on its own).

Those are covered by the layers that don't depend on parsing: there are no AWS or cluster
credentials to use, the token can't touch workflows or settings, `main` needs a reviewed PR,
`.env` holds nothing the agent needs (and `ANTHROPIC_API_KEY` can stay out of it entirely; see
the spec's reviewer setup), and a human reviews every PR, including any script the agent adds.

### Adding to the rules

- New hard rule in `AGENTS.md` → add a check in `guard.py`, blocked and allowed cases in
  `agent-hooks/tests/test_guard.py`, a mirror in `permissions.deny` if a simple pattern fits,
  and a row in the tables here and in [agent-extension-pack.md](agent-extension-pack.md).
- A false positive → add the command to the `ALLOWED` list in the tests first, then fix the guard.
- New `make` test or lint target → add its own exact `Bash(make <target>)` entry to
  `permissions.allow`. Never use a wildcard on `make`: an allow rule matches the rest of the
  line, so `Bash(make test-*)` would also let `make test-hooks down` run without a prompt. The
  guard can't see what a `make` target does, so this matters.
- **No `make` target that touches AWS or a cluster is ever added to `permissions.allow`**: not
  `up`, `down`, `addons`, `deploy`, `revert`, `demo-*`, `domain*`, `bootstrap`, `audit-rbac`, or
  `test-k8s` (kind). They always prompt, and the agent's own PRs follow this rule too.

## Kubernetes RBAC

Everything the `consoletrack` chart grants is a namespaced Role; the app charts create no
ClusterRole or ClusterRoleBinding (`helm/tests/test_rendered.py` checks the rendered output).

| Subject | Role, namespaces | Allowed | Not allowed |
| --- | --- | --- | --- |
| ServiceAccount `consoletrack:k8s-mcp` | `k8s-mcp-read` in each of `allowedNamespaces` (`demo`) | get, list, watch on pods, pods/log, events, deployments, replicasets, statefulsets | Secrets, ConfigMaps, exec, attach, port-forward, any write, any other namespace |
| ServiceAccount `consoletrack:remediator` (the backend) | `remediator` in each of `allowedNamespaces` | get, patch on deployments; get, list on replicasets | create, delete, exec, Secrets, cluster-scoped resources |
| Group `consoletrack-deployers` (CI's deploy role, through its EKS access entry) | `consoletrack-deployer` in `consoletrack` and `demo` | Manage the kinds the app charts create, including Helm's release records (ConfigMaps, `HELM_DRIVER=configmap`); read pods, logs, events; port-forward for the smoke test | Secrets, anything cluster-scoped (CRDs, add-ons, namespaces), other namespaces, exec |

Every other pod runs with `automountServiceAccountToken: false`: the worker, `aws-mcp`, and the
canary reach AWS through Pod Identity's own projected token, and the frontend and demo app call
nothing. `make audit-rbac` prints the effective permissions per ServiceAccount.

## CI deploy role

`deploy.yaml`'s OIDC role is mapped to the `consoletrack-deployers` group, bound to a namespaced
Role in `consoletrack` and `demo` (above), and the workflow only ever runs
`helmfile -l tier=app apply` (the `consoletrack` and `demo-app` releases). The bindings come
from the `consoletrack` chart itself, so the first `make deploy` is a human's. The `tier=addons` releases (Gateway API CRDs, kube-prometheus-stack, cloudnative-pg,
the Load Balancer Controller, ExternalDNS) need cluster-wide rights, so only a human applies
them, with `make addons`. Terraform never installs anything inside Kubernetes.
