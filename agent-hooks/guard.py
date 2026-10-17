#!/usr/bin/env python3
"""PreToolUse hook: enforce the AGENTS.md hard rules before a tool call runs.

Origin: extended from ``05-agent-capabilities/agent-hooks/guard_branch_protection.py``
in jennasrunbooks/ai-dev-tools-zoomcamp-2026 (the Module 5 hook that blocks
``git commit``/``git push`` on ``main``). That hook matched one regex against the
raw command string; this one tokenizes the command so that chained, wrapped and
nested forms are checked too, and it covers the other rules a tool call can break:

- commits/pushes on ``main``, pushes that target ``main``, force pushes, branch deletes
- Terraform state/infra writes, mutating ``kubectl``, ``helm`` and ``helmfile``, AWS
  writes and credential setup, ``eksctl``
- edits to ``*.tf`` files that add the Terraform ``helm`` or ``kubernetes`` provider
- reading or writing ``.env`` / ``.env.*`` (``.env.example`` is fine)
- edits under ``.github/workflows/``
- creating ``CLAUDE.md`` / ``CLAUDE.local.md``, which would stop Claude Code loading AGENTS.md
- merging PRs and changing secrets, variables, workflows or repo settings with ``gh``

Hook contract: the tool call arrives as JSON on stdin. Exit 2 with a message on
stderr blocks the call and shows the message to the agent; exit 0 allows it.

Standard library only: it must run before ``uv sync``. It is defense in depth, not
the only control; see docs/permissions.md for what it can't see.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath

POINTER = "See AGENTS.md → Hard rules."

RULE_INFRA = "Rule 1 (never change real infrastructure)"
RULE_CREDS = "Rule 2 (no AWS or cluster credentials)"
RULE_ENV = "Rule 3 (never read or print .env files or secrets)"
RULE_WORKFLOWS = "Rule 4 (don't edit .github/workflows/)"
RULE_MAIN = "Rule 5 (never commit to main)"
RULE_REPO = "Rule 5 (never merge your own PR or change repo settings)"
RULE_INSTRUCTIONS = "AGENTS.md is the only project instructions file"

PROTECTED_BRANCHES = {"main", "master"}
MAX_DEPTH = 8


class Blocked(Exception):
    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(f"{rule}: {detail}")
        self.rule = rule
        self.detail = detail


def message(err: Blocked) -> str:
    return f"Blocked by agent-hooks/guard.py. {err.rule}: {err.detail} {POINTER}"


# --------------------------------------------------------------------------- paths


def project_dir(payload: dict) -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()


def relative_to_project(path: str, cwd: str, project: str) -> PurePosixPath:
    """Return ``path`` relative to the project when it is inside it, else as absolute."""
    full = os.path.realpath(os.path.join(cwd, os.path.expanduser(path)))
    root = os.path.realpath(project)
    if full == root or full.startswith(root + os.sep):
        return PurePosixPath(os.path.relpath(full, root))
    return PurePosixPath(full)


_ENV_NAME = re.compile(r"^\.env($|[.*?\[])")


def is_env_file(name: str) -> bool:
    base = name.rstrip("/").rsplit("/", 1)[-1]
    return bool(_ENV_NAME.match(base)) and base != ".env.example"


def is_workflow_path(path: PurePosixPath) -> bool:
    parts = path.parts
    return any(parts[i : i + 2] == (".github", "workflows") for i in range(len(parts) - 1))


def is_instructions_file(path: PurePosixPath) -> bool:
    return path.name.lower() in {"claude.md", "claude.local.md"}


def check_write_target(path: str, cwd: str, project: str) -> None:
    rel = relative_to_project(path, cwd, project)
    if is_workflow_path(rel):
        raise Blocked(
            RULE_WORKFLOWS,
            f"'{rel}' is under .github/workflows/. Put the proposed file in "
            "docs/proposed-workflows/<name>.yaml and say in the PR that a human needs to move it.",
        )
    if is_instructions_file(rel):
        raise Blocked(
            RULE_INSTRUCTIONS,
            f"writing '{rel}' would stop Claude Code loading AGENTS.md. Edit AGENTS.md instead.",
        )


# ------------------------------------------------------------------------ file tools

FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def check_file_tool(tool: str, tool_input: dict, cwd: str, project: str) -> None:
    for key in ("file_path", "notebook_path", "path"):
        path = tool_input.get(key)
        if not path:
            continue
        if is_env_file(path):
            raise Blocked(RULE_ENV, f"{tool} of '{path}' is not allowed (.env.example is fine).")
        if tool in WRITE_TOOLS:
            check_write_target(path, cwd, project)
    if tool in WRITE_TOOLS:
        check_terraform_providers(tool, tool_input)
    glob = tool_input.get("glob")
    if tool == "Grep" and glob and is_env_file(glob):
        raise Blocked(RULE_ENV, f"Grep over '{glob}' is not allowed.")


# ------------------------------------------------------------------------- tokenizer


@dataclass
class Segment:
    """One simple command: its text plus any heredoc bodies fed to it."""

    text: str
    heredocs: list[str] = field(default_factory=list)


@dataclass
class Parsed:
    segments: list[Segment]
    substitutions: list[str]


def _matching_paren(s: str, start: int) -> int:
    """Index of the ``)`` closing the ``$(`` whose ``(`` is at ``start``, or len(s)."""
    depth, i, quote = 0, start, None
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = None
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(s)


_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def split_command(cmd: str) -> Parsed:
    """Split a shell command on unquoted ``; & && || | |& ( )`` and newlines.

    Command substitutions (``$(...)`` and backticks) are collected separately so the
    caller can check them as commands of their own. Heredoc bodies are attached to the
    segment that reads them instead of being treated as commands.
    """
    segments: list[Segment] = []
    subs: list[str] = []
    buf: list[str] = []
    pending: list[tuple[str, bool]] = []  # heredoc delimiters waiting for the next newline
    current = Segment("")
    quote: str | None = None
    i, n = 0, len(cmd)

    def flush() -> None:
        nonlocal current
        current.text = "".join(buf).strip()
        if current.text or current.heredocs:
            segments.append(current)
        buf.clear()
        current = Segment("")

    while i < n:
        c = cmd[i]
        if quote == "'":
            buf.append(c)
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(" " if cmd[i + 1] == "\n" else cmd[i : i + 2])  # line continuation
            i += 2
            continue
        if c == "$" and cmd.startswith("$(", i) and not cmd.startswith("$((", i):
            end = _matching_paren(cmd, i + 1)
            subs.append(cmd[i + 2 : end])
            buf.append(cmd[i : end + 1])
            i = end + 1
            continue
        if c == "`":
            end = cmd.find("`", i + 1)
            end = n if end == -1 else end
            subs.append(cmd[i + 1 : end])
            buf.append(cmd[i : end + 1])
            i = end + 1
            continue
        if quote == '"':
            buf.append(c)
            if c == '"':
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "<" and cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = _HEREDOC.match(cmd, i)
            if m:
                pending.append((m.group(3), m.group(1) == "-"))
                buf.append(m.group(0))
                i = m.end()
                continue
        if c == "\n":
            if pending:
                seg = current
                i += 1
                for delim, strip_tabs in pending:
                    body: list[str] = []
                    while i < n:
                        end = cmd.find("\n", i)
                        end = n if end == -1 else end
                        line = cmd[i:end]
                        i = end + 1
                        if (line.lstrip("\t") if strip_tabs else line) == delim:
                            break
                        body.append(line)
                    seg.heredocs.append("\n".join(body))
                pending.clear()
                flush()
                continue
            flush()
            i += 1
            continue
        prev = cmd[i - 1] if i else ""
        nxt = cmd[i + 1] if i + 1 < n else ""
        if c == "&" and nxt != "&" and (prev in "<>" or nxt == ">"):
            buf.append(c)  # redirection such as 2>&1 or &>file
            i += 1
            continue
        if c == "|" and prev == ">":
            buf.append(c)  # >| clobber redirection
            i += 1
            continue
        if c in ";&|()":
            flush()
            i += 2 if cmd[i : i + 2] in ("&&", "||", "|&", ";;") else 1
            continue
        buf.append(c)
        i += 1
    flush()
    return Parsed(segments, subs)


def tokenize(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


# ----------------------------------------------------------------- argv normalizing

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Wrapper -> options that take a separate value.
# fmt: off
WRAPPERS: dict[str, set[str]] = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "--unset", "--chdir"},
    "command": set(),
    "builtin": set(),
    "exec": {"-a"},
    "time": {"-f", "-o", "--format", "--output"},
    "nohup": set(),
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p"},
    "stdbuf": {"-i", "-o", "-e"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--arg-file", "--delimiter"},
    "watch": {"-n", "-d", "--interval"},
}
UV_RUN_VALUE_OPTS = {
    "--with", "--with-requirements", "--with-editable", "--project", "--directory", "--python",
    "-p", "--package", "--extra", "--group", "--only-group", "--no-group", "--env-file",
    "--index", "--default-index", "--index-url", "--extra-index-url", "--find-links", "-f",
    "--script", "--config-file", "--cache-dir",
}
# fmt: on
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}


def _skip_options(argv: list[str], value_opts: set[str]) -> list[str]:
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            return argv[i + 1 :]
        if not tok.startswith("-") or tok == "-":
            break
        i += 2 if tok in value_opts else 1
    return argv[i:]


def unwrap(argv: list[str]) -> list[str]:
    """Strip leading ``VAR=value`` assignments and wrapper commands."""
    while argv:
        if _ASSIGNMENT.match(argv[0]):
            argv = argv[1:]
            continue
        name = os.path.basename(argv[0])
        if name in ("{", "!", "then", "do", "else", "elif", "if", "while", "until"):
            argv = argv[1:]
            continue
        if name == "uv" and len(argv) > 1 and argv[1] == "run":
            argv = _skip_options(argv[2:], UV_RUN_VALUE_OPTS)
            continue
        if name in WRAPPERS:
            if name == "command" and any(t in ("-v", "-V") for t in argv[1:2]):
                return []  # `command -v x` only looks x up
            if name == "env":
                split = next((j for j, t in enumerate(argv) if t in ("-S", "--split-string")), 0)
                if split and split + 1 < len(argv):
                    argv = tokenize(argv[split + 1]) + argv[split + 2 :]
                    continue
            rest = _skip_options(argv[1:], WRAPPERS[name])
            if name == "timeout" and rest:
                rest = rest[1:]  # the duration
            argv = rest
            continue
        break
    return argv


# ----------------------------------------------------------------------- git rules


def current_branch(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "-q", "HEAD"],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


# fmt: off
GIT_VALUE_OPTS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env",
    "--super-prefix",
}
# fmt: on


@dataclass
class Context:
    cwd: str
    project: str
    branch_override: dict[str, str] = field(default_factory=dict)

    def branch(self, cwd: str) -> str | None:
        key = os.path.normpath(cwd)
        return self.branch_override.get(key) or current_branch(cwd)


def _strip_ref(ref: str) -> str:
    ref = ref.lstrip("+")
    for prefix in ("refs/heads/", "heads/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def check_git(args: list[str], ctx: Context) -> None:
    cwd = ctx.cwd
    i = 0
    while i < len(args) and args[i].startswith("-"):
        tok = args[i]
        if tok in GIT_VALUE_OPTS and i + 1 < len(args):
            if tok == "-C":
                cwd = os.path.join(cwd, args[i + 1])
            i += 2
            continue
        i += 1
    if i >= len(args):
        return
    verb, rest = args[i], args[i + 1 :]

    if verb in ("checkout", "switch"):
        # Track `git switch -c x && git commit` so the commit isn't checked against the
        # branch we are about to leave. `git checkout <name>` may restore a file instead,
        # so it only counts when it lands on a protected branch.
        new = None
        for j, tok in enumerate(rest):
            if tok in ("-b", "-B", "-c", "-C", "--orphan") and j + 1 < len(rest):
                new = rest[j + 1]
                break
        if new is None and "--" not in rest:
            positional = [t for t in rest if not t.startswith("-")]
            if len(positional) == 1 and (verb == "switch" or positional[0] in PROTECTED_BRANCHES):
                new = positional[0]
        if new:
            ctx.branch_override[os.path.normpath(cwd)] = new
        return

    if verb not in ("commit", "push"):
        return
    branch = ctx.branch(cwd)
    if branch in PROTECTED_BRANCHES:
        raise Blocked(
            RULE_MAIN,
            f"'git {verb}' while on '{branch}'. Create a branch first "
            "(git switch -c feat/<area>-<short-desc>), then retry.",
        )
    if verb == "commit":
        return

    positional: list[str] = []
    j = 0
    while j < len(rest):
        tok = rest[j]
        if tok in (
            "--force",
            "--force-with-lease",
            "--force-if-includes",
        ) or tok.startswith("--force-with-lease="):
            raise Blocked(RULE_MAIN, f"force push ('{tok}') is not allowed.")
        if tok in ("--delete", "--mirror", "--prune", "--all", "--branches"):
            raise Blocked(
                RULE_MAIN,
                f"'git push {tok}' can push to or delete branches other than yours.",
            )
        if tok in ("-o", "--push-option", "--repo", "--receive-pack", "--exec"):
            j += 2
            continue
        if tok.startswith("-") and not tok.startswith("--"):
            flags = tok[1:]
            if "f" in flags:
                raise Blocked(RULE_MAIN, f"force push ('{tok}') is not allowed.")
            if "d" in flags:
                raise Blocked(RULE_MAIN, f"'git push {tok}' deletes a remote branch.")
        elif not tok.startswith("-"):
            positional.append(tok)
        j += 1

    for refspec in positional[1:]:
        if refspec.startswith("+"):
            raise Blocked(RULE_MAIN, f"force push ('{refspec}') is not allowed.")
        src, sep, dst = refspec.partition(":")
        if sep and not src:
            raise Blocked(RULE_MAIN, f"'{refspec}' deletes a remote branch.")
        target = _strip_ref(dst or src)
        if target in PROTECTED_BRANCHES:
            raise Blocked(RULE_MAIN, f"'{refspec}' pushes to '{target}'. Open a PR instead.")


# ---------------------------------------------------------------- terraform rules

TF_WRITE_VERBS = {"apply", "destroy", "import", "taint", "untaint", "force-unlock", "refresh"}
TF_STATE_WRITES = {"rm", "mv", "push", "replace-provider"}
TF_WORKSPACE_WRITES = {"new", "delete", "select"}


def check_terraform(args: list[str]) -> None:
    if args[:1] == ["-chdir"]:
        args = args[2:]  # `-chdir=dir` is the documented form; tolerate `-chdir dir`
    positional = [t for t in args if not t.startswith("-")]
    if not positional:
        return
    verb, sub = positional[0], positional[1] if len(positional) > 1 else ""
    if verb in TF_WRITE_VERBS:
        raise Blocked(
            RULE_INFRA,
            f"'terraform {verb}' changes real infrastructure or state. "
            "Ship the change as code in a PR; CI posts the plan and a human applies.",
        )
    if verb == "state" and sub in TF_STATE_WRITES:
        raise Blocked(RULE_INFRA, f"'terraform state {sub}' writes state.")
    if verb == "workspace" and sub in TF_WORKSPACE_WRITES:
        raise Blocked(RULE_INFRA, f"'terraform workspace {sub}' changes the active state.")


# ------------------------------------------------------------------ kubectl rules

# fmt: off
KUBECTL_VALUE_OPTS = {
    "-n", "--namespace", "--context", "--cluster", "--user", "--kubeconfig", "-s", "--server",
    "--token", "--as", "--as-group", "--as-uid", "--certificate-authority",
    "--client-certificate", "--client-key", "--request-timeout", "--tls-server-name",
    "--cache-dir", "-v", "--v", "--log-file", "--profile", "--profile-output",
}
KUBECTL_WRITE_VERBS = {
    "apply", "create", "delete", "edit", "patch", "replace", "scale", "rollout", "label",
    "annotate", "drain", "cordon", "uncordon", "taint", "exec", "cp", "run", "set", "autoscale",
    "expose", "attach", "debug", "certificate",
}
# fmt: on
KUBECTL_ROLLOUT_READS = {"status", "history"}


def first_positional(args: list[str], value_opts: set[str]) -> tuple[str, list[str]]:
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("-"):
            i += 2 if tok in value_opts else 1
            continue
        return tok, args[i + 1 :]
    return (args[i], args[i + 1 :]) if i < len(args) else ("", [])


def check_kubectl(args: list[str]) -> None:
    verb, rest = first_positional(args, KUBECTL_VALUE_OPTS)
    if verb == "rollout":
        sub, _ = first_positional(rest, KUBECTL_VALUE_OPTS)
        if sub in KUBECTL_ROLLOUT_READS:
            return
    if verb in KUBECTL_WRITE_VERBS:
        raise Blocked(
            RULE_INFRA,
            f"'kubectl {verb}' changes the cluster. Use read-only "
            "commands (get, describe, logs); cluster changes ship as Helm code in a PR.",
        )


# --------------------------------------------------------------------- helm rules

# fmt: off
HELM_VALUE_OPTS = {
    "-n", "--namespace", "--kube-context", "--kubeconfig", "--kube-apiserver", "--kube-token",
    "--kube-as-user", "--kube-as-group", "--kube-ca-file", "--kube-tls-server-name",
    "--registry-config", "--repository-cache", "--repository-config", "--burst-limit", "--qps",
}
HELM_WRITE_VERBS = {"install", "upgrade", "uninstall", "un", "delete", "del", "rollback", "test"}
# fmt: on


def check_helm(args: list[str]) -> None:
    verb, _ = first_positional(args, HELM_VALUE_OPTS)
    if verb in HELM_WRITE_VERBS:
        raise Blocked(
            RULE_INFRA,
            f"'helm {verb}' changes the cluster. Use helm lint/template; releases ship through CI.",
        )


# ----------------------------------------------------------------- helmfile rules

# fmt: off
HELMFILE_VALUE_OPTS = {
    "-f", "--file", "-e", "--environment", "-l", "--selector", "-n", "--namespace",
    "--kube-context", "--kubeconfig", "-b", "--helm-binary", "-k", "--kustomize-binary",
    "-c", "--chart", "--state-values-set", "--state-values-set-string", "--state-values-file",
    "--log-level", "--args",
}
# Allowlist, not denylist: `apply`, `sync`, `destroy`, `delete`, `init` (installs plugins),
# `test`, the deprecated `charts` (= sync), and anything new or unknown are blocked. `diff` only
# reads, and fails anyway without a kubeconfig; `deps`, `repos`, `fetch` write local files only.
HELMFILE_READ_VERBS = {
    "lint", "template", "build", "list", "deps", "version", "write-values", "diff", "status",
    "repos", "fetch", "show-dag", "print-env", "cache", "completion", "help",
}
# fmt: on


def check_helmfile(args: list[str]) -> None:
    verb, rest = first_positional(args, HELMFILE_VALUE_OPTS)
    if verb and verb not in HELMFILE_READ_VERBS:
        raise Blocked(
            RULE_INFRA,
            f"'helmfile {verb}' changes the cluster or installs plugins. Use helmfile "
            "lint/template/diff; releases ship through CI (`helmfile -l tier=app apply`) "
            "or a human's `make addons`/`make deploy`.",
        )
    # Arguments handed through to helm: after `--`, or in `--args "…"`.
    passthrough = rest[rest.index("--") + 1 :] if "--" in rest else []
    for j, tok in enumerate(args):
        if tok == "--args" and j + 1 < len(args):
            passthrough += tokenize(args[j + 1])
        elif tok.startswith("--args="):
            passthrough += tokenize(tok.split("=", 1)[1])
    hit = next((t for t in passthrough if t in HELM_WRITE_VERBS), None)
    if hit:
        raise Blocked(RULE_INFRA, f"'helmfile {verb}' passes '{hit}' through to helm.")


# --------------------------------------------------------- terraform providers

# Terraform is AWS-only: Helmfile owns every chart, so these providers never come back.
_K8S_PROVIDER = re.compile(r'provider\s+"(helm|kubernetes)"|hashicorp/(helm|kubernetes)\b')


def check_terraform_providers(tool: str, tool_input: dict) -> None:
    path = tool_input.get("file_path") or ""
    if not path.endswith(".tf"):
        return
    # (old, new) text pairs; a Write has no old text, so anything in its content counts as added.
    if tool == "MultiEdit":
        edits = tool_input.get("edits") or []
        pairs = [(e.get("old_string", ""), e.get("new_string", "")) for e in edits]
    else:
        new = tool_input.get("new_string") or tool_input.get("content", "")
        pairs = [(tool_input.get("old_string", ""), new)]
    for old, new in pairs:
        added = {m.group(0) for m in _K8S_PROVIDER.finditer(new or "")}
        added -= {m.group(0) for m in _K8S_PROVIDER.finditer(old or "")}
        if added:
            raise Blocked(
                RULE_INFRA,
                f"'{path}' would add '{sorted(added)[0]}'. Terraform manages AWS only and never "
                "installs anything inside Kubernetes; every chart is a release in "
                "helm/helmfile.yaml.gotmpl (AGENTS.md → Conventions → Helm).",
            )


# ---------------------------------------------------------------------- aws rules

# fmt: off
AWS_VALUE_OPTS = {
    "--profile", "--region", "--output", "--endpoint-url", "--query", "--cli-read-timeout",
    "--cli-connect-timeout", "--ca-bundle", "--color", "--cli-binary-format",
}
# Allowlist, not denylist: anything that isn't clearly a read is blocked.
AWS_READ_PREFIXES = (
    "describe-", "get-", "list-", "lookup-", "search-", "batch-get-", "head-", "simulate-",
    "validate-", "filter-",
)
AWS_READ_OPS = {"query", "scan", "wait", "help", "tail"}
AWS_CREDENTIAL_SETUP = {"configure", "login", "sso"}
AWS_CREDENTIAL_OPS = {
    "get-session-token", "get-federation-token", "get-login-password",
    "get-authorization-token", "get-role-credentials", "get-credentials-for-identity",
    "get-token", "update-kubeconfig",
}
# fmt: on


def check_aws(args: list[str]) -> None:
    service, rest = first_positional(args, AWS_VALUE_OPTS)
    if not service or service == "help":
        return
    op, _ = first_positional(rest, AWS_VALUE_OPTS)
    call = f"aws {service} {op}".strip()
    if service in AWS_CREDENTIAL_SETUP and not (service == "sso" and op == "logout"):
        raise Blocked(
            RULE_CREDS,
            f"'{call}' sets up credentials. The agent works without AWS credentials on purpose.",
        )
    if op.startswith("assume-role") or op in AWS_CREDENTIAL_OPS:
        raise Blocked(RULE_CREDS, f"'{call}' obtains or stores credentials.")
    if service == "s3":
        if op in ("ls", "help", ""):
            return
        raise Blocked(RULE_INFRA, f"'aws s3 {op}' writes to S3. Only 'aws s3 ls' is allowed.")
    if not op or op in AWS_READ_OPS or op.startswith(AWS_READ_PREFIXES):
        return
    raise Blocked(
        RULE_INFRA,
        f"'aws {service} {op}' is not a read-only call. Only "
        "describe-*, get-*, list-* and similar reads are allowed; AWS changes "
        "ship as Terraform in a PR.",
    )


# ----------------------------------------------------------------------- gh rules

GH_BLOCKED = {
    ("pr", "merge"): "merging is a human's job; open the PR and wait for review.",
    ("api", ""): "'gh api' can write anything the token can; use gh pr/issue view.",
    ("secret", ""): "secrets are managed by a human.",
    ("variable", ""): "repo variables are managed by a human.",
    ("repo", "edit"): "repo settings are managed by a human.",
    ("repo", "delete"): "repo settings are managed by a human.",
    ("repo", "rename"): "repo settings are managed by a human.",
    ("repo", "archive"): "repo settings are managed by a human.",
    ("workflow", "run"): "workflows are dispatched by a human.",
    ("workflow", "enable"): "workflows are managed by a human.",
    ("workflow", "disable"): "workflows are managed by a human.",
    ("auth", "token"): "printing the GitHub token is not allowed.",
}
GH_VALUE_OPTS = {"-R", "--repo", "--hostname"}


def check_gh(args: list[str]) -> None:
    group, rest = first_positional(args, GH_VALUE_OPTS)
    sub, _ = first_positional(rest, GH_VALUE_OPTS)
    reason = GH_BLOCKED.get((group, sub)) or GH_BLOCKED.get((group, ""))
    if reason:
        rule = RULE_ENV if group == "auth" else RULE_REPO
        call = f"gh {group} {sub}".strip()
        raise Blocked(rule, f"'{call}': {reason}")


# -------------------------------------------------------------- bash file writes

_REDIRECT = re.compile(r"^(?:\d*|&)>>?\|?(.*)$")


def write_targets(argv: list[str]) -> list[str]:
    """Paths a simple command writes to: redirections, tee, cp/mv/ln/install, sed -i, ..."""
    targets: list[str] = []
    words: list[str] = []
    it = iter(range(len(argv)))
    for i in it:
        tok = argv[i]
        m = _REDIRECT.match(tok)
        if m:
            rest = m.group(1)
            if rest and not rest.startswith("&"):
                targets.append(rest)
            elif not rest and i + 1 < len(argv):
                targets.append(argv[i + 1])
                next(it, None)
            continue
        words.append(tok)
    if not words:
        return targets
    name = os.path.basename(words[0])
    args = [t for t in words[1:] if not t.startswith("-")]
    if name == "tee" or name in ("touch", "truncate", "rm", "mkdir", "chmod", "chown"):
        targets += args
    elif name in ("cp", "mv", "ln", "install", "rsync") and args:
        targets.append(args[-1])
        if "-t" in words or any(t.startswith("--target-directory") for t in words):
            targets += args
    elif name in ("sed", "perl") and any(t.startswith("-i") for t in words[1:]):
        targets += args[1:]
    return targets


# ---------------------------------------------------------------------- dispatch


def check_env_tokens(argv: list[str], raw: list[str]) -> None:
    """Block any reference to a .env file, except copying .env.example to a new file."""
    hits = []
    for tok in raw:
        if any(ch.isspace() for ch in tok):
            continue  # a message or quoted script, not a path
        for value in {tok, tok.split("=", 1)[-1]}:
            if is_env_file(value):
                hits.append(value)
    if not hits:
        return
    if argv and os.path.basename(argv[0]) == "cp":
        args = [t for t in argv[1:] if not t.startswith("-")]
        sources = args[:-1]
        if sources and all(PurePosixPath(s).name == ".env.example" for s in sources):
            return
    raise Blocked(
        RULE_ENV,
        f"'{hits[0]}' is a .env file. Use .env.example for documentation; "
        "secrets stay out of the agent's context.",
    )


def check_argv(raw: list[str], ctx: Context, heredocs: list[str], depth: int) -> None:
    argv = unwrap(raw)
    check_env_tokens(argv, raw)
    for target in write_targets(argv):
        check_write_target(target, ctx.cwd, ctx.project)
    if not argv:
        return
    name = os.path.basename(argv[0])
    args = argv[1:]

    if name in SHELLS:
        # `bash -c '...'`, `bash <<< '...'`, or a heredoc fed to the shell (directly or
        # through a pipe). A script file the shell runs is not visible here.
        scripts = []
        for j, tok in enumerate(args):
            if (re.fullmatch(r"-[a-z]*c[a-z]*", tok) or tok == "<<<") and j + 1 < len(args):
                scripts.append(args[j + 1])
            elif tok.startswith("<<<") and len(tok) > 3:
                scripts.append(tok[3:])
        for script in scripts or heredocs:
            check_command(script, ctx, depth + 1)
        return
    if name == "eval":
        check_command(" ".join(args), ctx, depth + 1)
        return
    if name == "find":
        for j, tok in enumerate(args):
            if tok in ("-exec", "-execdir", "-ok", "-okdir"):
                inner = []
                for t in args[j + 1 :]:
                    if t in (";", "+", "\\;"):
                        break
                    inner.append(t)
                check_argv(inner, ctx, [], depth + 1)
        return
    if name == "cd":
        target = next((t for t in args if not t.startswith("-")), os.path.expanduser("~"))
        ctx.cwd = os.path.normpath(os.path.join(ctx.cwd, os.path.expanduser(target)))
        return

    if name == "git":
        check_git(args, ctx)
    elif name in ("terraform", "tofu"):
        check_terraform(args)
    elif name == "kubectl":
        check_kubectl(args)
    elif name == "helm":
        check_helm(args)
    elif name == "helmfile":
        check_helmfile(args)
    elif name == "aws":
        check_aws(args)
    elif name == "eksctl":
        raise Blocked(RULE_INFRA, "eksctl is not used here; the cluster is managed by Terraform.")
    elif name == "gh":
        check_gh(args)


def check_command(cmd: str, ctx: Context, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise Blocked(RULE_INFRA, "command nests shells or substitutions too deeply to check.")
    parsed = split_command(cmd)
    for sub in parsed.substitutions:
        check_command(sub, ctx, depth + 1)
    heredocs = [body for seg in parsed.segments for body in seg.heredocs]
    for seg in parsed.segments:
        check_argv(tokenize(seg.text), ctx, heredocs, depth)


def check(payload: dict) -> None:
    """Raise Blocked if the tool call breaks a hard rule."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    project = project_dir(payload)
    cwd = payload.get("cwd") or project
    if tool == "Bash":
        check_command(tool_input.get("command", ""), Context(cwd=cwd, project=project))
    elif tool in FILE_TOOLS:
        check_file_tool(tool, tool_input, cwd, project)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # not a tool call we can read; don't break unrelated tools
    try:
        check(payload)
    except Blocked as err:
        print(message(err), file=sys.stderr)
        return 2
    except Exception as err:  # fail closed: a crash must not turn into "allowed"
        print(
            f"Blocked by agent-hooks/guard.py: internal error ({err!r}). {POINTER}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
