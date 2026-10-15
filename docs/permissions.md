# Permissions

Who can do what in ConsoleTrack. This page starts with the development-time agent (Claude Code
working on this repo). The runtime identities (the triage agent's MCP servers, the backend's
apply ServiceAccount, the CI plan/apply/revert roles) are added with the Terraform and Helm
issues that create them; the spec's "Identity and access" section is the plan.

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
| `permissions.allow` | `.claude/settings.json` | Nothing; it removes prompts for routine, safe commands (`make test`, `make lint`, `terraform fmt`, `helm lint`, read-only `git` and `gh`). | — |
| `permissions.deny` | `.claude/settings.json` | Backup for the guard: reading `.env` and the common `.env.<name>` variants (listed one by one, because deny globs can't exclude `.env.example`, which the agent maintains), editing `.github/workflows/` and `CLAUDE.md`, the Terraform/kubectl/helm write commands, `aws configure`, `aws sso login`, `eksctl`, force pushes, `gh pr merge`, `gh api`, `gh secret`, `gh variable`, `gh repo edit`, `gh workflow run`. See "Guard and deny rules" below for which one answers first. | Commands it can't pattern-match: flags in another order, wrappers, nested shells. The guard covers those. |
| PreToolUse guard | `agent-hooks/guard.py` | Tokenizes each command and file path and blocks everything in `AGENTS.md → Hard rules` that a tool call can break, including chained, wrapped, and nested forms. See [agent-extension-pack.md](agent-extension-pack.md#hooks). | See "What the guard can't see" below. |
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
