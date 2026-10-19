# Agent extension pack

The files that shape how Claude Code works in this repo. `AGENTS.md` says what the agent may
do; the pieces below make that true, give it reusable workflows, and document who can do what.
See [permissions.md](permissions.md) for every permission layer side by side.

| Piece | Location | Status |
| --- | --- | --- |
| Project instructions | `AGENTS.md` (the only instructions file; no `CLAUDE.md`) | Done |
| Hooks | `agent-hooks/`, wired in `.claude/settings.json` | Done (this page) |
| Permission rules | `.claude/settings.json` → `permissions` | Done |
| Permission notes | `docs/permissions.md` | Dev-time agent done; runtime identities come with the Terraform and Helm issues |
| Skills | `agent-capabilities/add-endpoint/`, `agent-capabilities/break-demo/` | Planned |
| Subagent | `custom-agent/k8s-manifest-reviewer.md` | Planned |
| MCP servers | `mcp-server/k8s/` and `.mcp.json` (this page); `mcp-server/aws/` | `k8s-mcp` done; `aws-mcp` with #9 |

## Installing

Nothing to install. Open the repo in the devcontainer (or any checkout) and start Claude Code
from the repo root: it loads `.claude/settings.json`, which runs the hooks with the system
`python3`. The hooks use the standard library only, so they work before `uv sync`.
`.claude/settings.local.json` is for personal overrides and is gitignored.

Check it works: `/hooks` lists both hooks, `/memory` lists `AGENTS.md`, and asking the agent to
run `terraform apply` comes back with a block message.

## MCP servers

### `k8s-mcp` through `.mcp.json`

The same read-only server the triage worker uses in the cluster, wired into Claude Code for
development against a local kind cluster. `.mcp.json` at the repo root starts it over stdio:

```json
"k8s-mcp": {
  "command": "uv",
  "args": ["run", "--locked", "--directory", "mcp-server/k8s", "python", "-m", "k8s_mcp"],
  "env": {"K8S_MCP_TRANSPORT": "stdio", "K8S_CONTEXT": "${K8S_MCP_CONTEXT:-kind-consoletrack}", …}
}
```

- **Where it runs.** On the host, next to kind: it needs a kubeconfig, and the devcontainer
  has none on purpose. Claude Code asks once before enabling a project MCP server; `/mcp`
  shows its status and the six tools.
- **What it can do.** Only the six reads in [mcp-server/k8s/README.md](../mcp-server/k8s/README.md#tools),
  in `K8S_ALLOWED_NAMESPACES` (default `demo`). Secrets and ConfigMaps are refused, env values
  are never returned, and logs and events arrive in `untrusted_data`, redacted. Every tool is
  annotated `readOnlyHint`.
- **Settings.** `K8S_MCP_CONTEXT`, `K8S_ALLOWED_NAMESPACES`, and `K8S_MCP_PROMETHEUS_URL`
  from the shell that starts Claude Code override the defaults.
- **Identity.** Locally it uses your kubeconfig context, so RBAC is whatever that context has;
  the in-server checks are what keep it read-only there. In the cluster it runs as the
  `k8s-mcp` ServiceAccount (`get`/`list`/`watch` only). See
  [permissions.md](permissions.md#triage-agent-runtime) and its accepted gaps.

Adding a tool: it must be a read, go in the spec's tool table, and come with the matching RBAC
change in the same PR (`AGENTS.md` → Hard rules, rule 6), plus a unit test and, for a new
resource, a `kubectl auth can-i` case in `mcp-server/k8s/tests/kind/`.

## Hooks

### `agent-hooks/guard.py` (PreToolUse)

Runs before every `Bash`, `Read`, `Edit`, `Write`, `MultiEdit`, `NotebookEdit`, and `Grep` call.
It exits `2` to block, with a message naming the rule and pointing to `AGENTS.md → Hard rules`;
Claude Code shows that message to the agent instead of running the call.

| Blocks | Examples | AGENTS.md rule |
| --- | --- | --- |
| Commit or push while on `main`/`master` | `git commit` on `main`, `git -C dir commit` | 5 |
| Pushes to `main`, force pushes, branch deletes | `git push origin HEAD:main`, `git push -f`, `git push --delete …`, `git push origin :x` | 5 |
| Terraform writes | `apply`, `destroy`, `import`, `taint`, `untaint`, `force-unlock`, `refresh`, `state rm/mv/push/replace-provider`, `workspace new/delete/select` | 1 |
| Mutating `kubectl` | `apply`, `create`, `delete`, `edit`, `patch`, `replace`, `scale`, `rollout` (except `status`/`history`), `label`, `annotate`, `drain`, `cordon`, `exec`, `cp`, `run`, … | 1 |
| Mutating `helm` | `install`, `upgrade`, `uninstall`, `rollback`, `test` | 1 |
| Mutating `helmfile` | anything but a known read (`lint`, `template`, `diff`, `deps`, `build`, `list`, `version`, `write-values`, …): `apply`, `sync`, `destroy`, `delete`, `init`, `test`, after any global flags (`-e`, `-l`, `--file`); helm write verbs passed through `--` or `--args` | 1 |
| Terraform `helm`/`kubernetes` provider | an `Edit`/`Write`/`MultiEdit` of a `*.tf` file that adds `provider "helm"`, `provider "kubernetes"`, `hashicorp/helm`, or `hashicorp/kubernetes` (Terraform is AWS-only; Helmfile owns every chart) | 1 |
| AWS writes | anything that isn't a read (`describe-*`, `get-*`, `list-*`, `lookup-*`, `simulate-*`, …); `aws s3` except `ls` | 1 |
| AWS credential setup | `aws configure`, `aws sso login`, `aws login`, `sts assume-role*`, `eks update-kubeconfig`, `get-session-token`, … | 2 |
| `eksctl` | any subcommand | 1 |
| `.env` files | `cat .env`, `source .env`, `--env-file=.env`; `Read`/`Edit`/`Write`/`Grep` of `.env` or `.env.*` (not `.env.example`; `cp .env.example .env` is allowed) | 3 |
| Workflow edits | file tools or shell writes (`>`, `tee`, `cp`, `sed -i`, …) under `.github/workflows/`; the message says to use `docs/proposed-workflows/` | 4 |
| Instruction files that hide `AGENTS.md` | creating or editing `CLAUDE.md`, `.claude/CLAUDE.md`, `CLAUDE.local.md` anywhere (reading is fine) | AGENTS.md is the only instructions file |
| Repo administration with `gh` | `gh pr merge`, `gh api`, `gh secret`, `gh variable`, `gh repo edit/delete/rename/archive`, `gh workflow run/enable/disable`, `gh auth token` | 5 (and 3 for the token) |

How it reads a command:

- Splits on `&&`, `||`, `;`, `|`, `&`, subshell parentheses, and newlines, and checks every part.
- Strips `VAR=value` prefixes and wrappers (`sudo`, `env`, `command`, `time`, `nohup`, `timeout`,
  `xargs`, `uv run`, …), and skips global flags with their values, so `kubectl -n ns delete`,
  `terraform -chdir=dir apply`, and `aws --profile p ec2 run-instances` are all found.
- Follows `bash -c '…'`, `sh -c`, `eval`, `env -S`, `find -exec`, `$(…)`, backticks,
  `bash <<< '…'`, and heredocs piped into a shell. Heredoc bodies fed to other commands (such as
  `cat <<'EOF'` for a commit message) are data and aren't checked as commands.
- Resolves file paths (relative, absolute, `..`, symlinks) against `$CLAUDE_PROJECT_DIR`.
- Tracks `git switch -c x && git commit` in one command, so the commit is checked against the new
  branch.
- Fails closed: if the guard itself crashes on an input, the call is blocked.

### `agent-hooks/lint_edited.py` (PostToolUse)

Runs after every `Edit`, `Write`, and `MultiEdit` and lints only that file. Findings go back to
the agent (exit `2` + stderr), so it fixes them in the same turn instead of at `make lint` time.

| File | Linter | Runs when |
| --- | --- | --- |
| `.py` | `ruff check` (`uvx ruff check` if ruff isn't on `PATH`) | ruff or uvx is installed |
| `.ts`, `.tsx`, `.js`, `.jsx` under `frontend/` | `npx --no-install eslint` | `frontend/node_modules` exists |
| `.tf`, `.tfvars` | `terraform fmt -check -diff` | terraform is installed |

A linter that isn't installed yet is skipped silently. The hook never changes files.

### Tests

`make test` runs `agent-hooks/tests/test_guard.py`: allowed and blocked command lists for every
rule (including the chained, wrapped, `-chdir`, `-n ns`, and `bash -c` forms), a commit-on-`main`
check against a throwaway repo, the file-tool cases, and the lint hook. When you add a rule, add
both a blocked and an allowed case.

### How the hooks fit with the other layers

The hooks are one layer of several, and not the strongest one. They stop honest mistakes and
most prompt-injected commands before anything runs, with a message the agent can act on. The
layers that hold even if a hook is bypassed are the ones outside the agent's process:

- **No credentials.** The devcontainer has no AWS keys or kubeconfig, so a write that slips past
  the guard fails with an auth error.
- **GitHub token.** The agent's fine-grained token has no Workflows or Administration permission,
  so it can't push workflow changes or change settings, whatever command it runs.
- **`main` ruleset.** Merging needs a reviewed PR and green CI, so a commit made on `main`
  locally can't land.
- **Environments.** Terraform apply and revert run only from GitHub environments that need a
  named reviewer (once the Terraform and revert workflows land).
- **`permissions.deny`** in `.claude/settings.json` mirrors the main guard rules as a backup
  pattern match. For Bash the guard answers first; for a denied file path the deny rule does.
  See [permissions.md](permissions.md#guard-and-deny-rules).

See [permissions.md](permissions.md) for what each layer stops and what the guard can't see.
