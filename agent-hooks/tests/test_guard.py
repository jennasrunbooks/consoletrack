"""Tests for agent-hooks/guard.py and agent-hooks/lint_edited.py.

Run with ``uvx pytest agent-hooks/tests -q`` (``make test`` does this).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1]
GUARD = HOOKS / "guard.py"
LINT = HOOKS / "lint_edited.py"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module  # dataclasses look the module up while it loads
    spec.loader.exec_module(module)
    return module


guard = _load(GUARD)


def _git_repo(path: Path, branch: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    return path


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A throwaway repo on a feature branch, used as the project dir."""
    root = _git_repo(tmp_path / "repo", "feat/backend-x")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


@pytest.fixture
def main_repo(tmp_path, monkeypatch) -> Path:
    root = _git_repo(tmp_path / "main-repo", "main")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


def bash(command: str, cwd: Path) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


def tool(name: str, cwd: Path, **tool_input) -> dict:
    return {"tool_name": name, "tool_input": tool_input, "cwd": str(cwd)}


def blocked(payload: dict) -> str | None:
    """The block message, or None if the call is allowed."""
    try:
        guard.check(payload)
    except guard.Blocked as err:
        return guard.message(err)
    return None


def run_hook(script: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
    )


# --------------------------------------------------------------------- Bash: allowed

ALLOWED = [
    # routine work
    "make test",
    "make lint",
    "uv run pytest",
    "cd backend && uv run pytest -m 'not integration'",
    "uv sync",
    "npm ci && npm run lint && npm test",
    "ls -la",
    "env",
    # terraform reads and checks
    "terraform fmt -check -recursive terraform/",
    "terraform -chdir=terraform/consoletrack/global init -backend=false",
    "terraform -chdir=terraform/consoletrack/global validate",
    "terraform -chdir=terraform/demo-app/us-east-1 plan",
    "terraform state list",
    "terraform state show module.x",
    "terraform workspace list",
    "terraform version",
    "tflint --chdir terraform/consoletrack/us-east-1",
    # helm and kubectl reads
    "helm lint helm/consoletrack",
    "helm template ct helm/consoletrack -n consoletrack",
    "helm -n consoletrack list",
    "kubectl get pods -A",
    "kubectl -n consoletrack get pods",
    "kubectl --context kind-ct -n demo describe deploy/demo-app",
    "kubectl logs -n demo deploy/demo-app --previous",
    "kubectl rollout history deploy/demo-app -n demo",
    "kubectl auth can-i --list --as system:serviceaccount:consoletrack:k8s-mcp",
    "kubectl get pods -o yaml | grep image",
    # aws reads
    "aws sts get-caller-identity",
    "aws --profile sandbox --region us-east-1 ec2 describe-security-groups",
    "aws iam get-role --role-name ct-x",
    "aws cloudtrail lookup-events --max-results 5",
    "aws s3 ls",
    "aws --version",
    # git and gh on a feature branch
    "git status",
    "git diff --stat",
    "git log --oneline -5",
    "git commit -m 'feat(intake): parse events'",
    "git push -u origin feat/backend-x",
    "git push origin HEAD",
    "git push",
    "git -C . commit -m x",
    "gh pr view 1",
    "gh issue view 1 --comments",
    "gh pr create --title x --body-file /tmp/body.md",
    # .env.example is fine
    "cp .env.example .env",
    "cat .env.example",
    "diff .env.example docs/env.md",
    "ls .envrc",
    # reading instruction files is fine
    "cat CLAUDE.md",
    "grep -n rule .claude/CLAUDE.md",
    # words inside messages are not commands
    'git commit -m "docs: never run terraform apply or cat .env"',
    "echo 'kubectl delete pod x' | wc -c",
    "git commit -m \"$(cat <<'EOF'\nterraform apply is blocked\nkubectl delete too\nEOF\n)\"",
    "cat > notes.txt <<'EOF'\nterraform destroy\nEOF",
    "git log 2>&1 | head",
    "command -v terraform",
    "python3 -c 'print(1)'",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_allowed(command, repo):
    assert blocked(bash(command, repo)) is None


# --------------------------------------------------------------------- Bash: blocked

BLOCKED = [
    # pushes that target main, force pushes, branch deletes (from a feature branch)
    ("git push origin main", "Rule 5"),
    ("git push origin HEAD:main", "Rule 5"),
    ("git push origin feat/backend-x:refs/heads/main", "Rule 5"),
    ("git push origin master", "Rule 5"),
    ("git push --force origin feat/backend-x", "Rule 5"),
    ("git push -f", "Rule 5"),
    ("git push -uf origin feat/backend-x", "Rule 5"),
    ("git push --force-with-lease", "Rule 5"),
    ("git push origin +feat/backend-x", "Rule 5"),
    ("git push --delete origin feat/old", "Rule 5"),
    ("git push origin -d feat/old", "Rule 5"),
    ("git push origin :feat/old", "Rule 5"),
    ("git push --mirror", "Rule 5"),
    ("git checkout main && git commit -m x", "Rule 5"),
    # terraform writes, with and without -chdir
    ("terraform apply", "Rule 1"),
    ("terraform apply -auto-approve", "Rule 1"),
    ("terraform -chdir=terraform/consoletrack/global apply", "Rule 1"),
    ("terraform -chdir=terraform/demo-app/us-east-1 destroy", "Rule 1"),
    ("terraform import aws_s3_bucket.x demo-app-x", "Rule 1"),
    ("terraform taint aws_instance.x", "Rule 1"),
    ("terraform untaint aws_instance.x", "Rule 1"),
    ("terraform force-unlock 1234", "Rule 1"),
    ("terraform refresh", "Rule 1"),
    ("terraform state rm aws_s3_bucket.x", "Rule 1"),
    ("terraform -chdir=x state mv a b", "Rule 1"),
    ("terraform state push x.tfstate", "Rule 1"),
    ("terraform state replace-provider a b", "Rule 1"),
    ("terraform workspace new dev", "Rule 1"),
    ("terraform workspace delete dev", "Rule 1"),
    ("terraform -chdir=x workspace select dev", "Rule 1"),
    ("/usr/local/bin/terraform apply", "Rule 1"),
    ("tofu apply", "Rule 1"),
    # mutating kubectl, with flags before the verb
    *[
        (f"kubectl {verb} x", "Rule 1")
        for verb in (
            "apply -f",
            "create ns",
            "delete pod",
            "edit deploy",
            "patch deploy",
            "replace -f",
            "scale deploy",
            "rollout restart deploy",
            "label pod",
            "annotate pod",
            "drain node",
            "cordon node",
            "exec -it pod --",
            "cp pod:/x",
            "run",
        )
    ],
    ("kubectl -n consoletrack delete pod x", "Rule 1"),
    ("kubectl --context kind-ct -n demo scale deploy/x --replicas 0", "Rule 1"),
    ("kubectl --namespace=demo apply -f x.yaml", "Rule 1"),
    ("kubectl get deploy x -o yaml | kubectl apply -f -", "Rule 1"),
    # mutating helm
    ("helm install ct helm/consoletrack", "Rule 1"),
    ("helm upgrade --install ct helm/consoletrack", "Rule 1"),
    ("helm -n consoletrack uninstall ct", "Rule 1"),
    ("helm rollback ct 1 -n consoletrack", "Rule 1"),
    ("helm --kube-context kind-ct -n demo upgrade demo helm/demo-app", "Rule 1"),
    # aws writes and credential setup
    ("aws configure", "Rule 2"),
    ("aws configure list", "Rule 2"),
    ("aws sso login --profile x", "Rule 2"),
    ("aws sts assume-role --role-arn x --role-session-name y", "Rule 2"),
    ("aws eks update-kubeconfig --name ct", "Rule 2"),
    ("aws ec2 create-security-group --group-name x", "Rule 1"),
    ("aws iam delete-role --role-name x", "Rule 1"),
    ("aws s3api put-object --bucket x --key y", "Rule 1"),
    ("aws iam update-assume-role-policy --role-name x", "Rule 1"),
    ("aws ec2 modify-instance-attribute --instance-id i", "Rule 1"),
    ("aws iam attach-role-policy --role-name x", "Rule 1"),
    ("aws iam detach-role-policy --role-name x", "Rule 1"),
    ("aws ec2 authorize-security-group-ingress --group-id sg", "Rule 1"),
    ("aws ec2 revoke-security-group-ingress --group-id sg", "Rule 1"),
    ("aws ec2 run-instances --image-id x", "Rule 1"),
    ("aws ec2 start-instances --instance-ids i", "Rule 1"),
    ("aws ec2 stop-instances --instance-ids i", "Rule 1"),
    ("aws ec2 terminate-instances --instance-ids i", "Rule 1"),
    ("aws ec2 create-tags --resources sg --tags Key=a,Value=b", "Rule 1"),
    ("aws lambda invoke --function-name x out.json", "Rule 1"),
    ("aws --profile p --region us-east-1 ec2 delete-security-group --group-id sg", "Rule 1"),
    ("aws ec2 --region us-east-1 delete-security-group --group-id sg", "Rule 1"),
    *[(f"aws s3 {op} s3://b/k .", "Rule 1") for op in ("cp", "mv", "rm", "sync")],
    ("aws s3 mb s3://b", "Rule 1"),
    ("aws s3 rb s3://b", "Rule 1"),
    # eksctl, any subcommand
    ("eksctl get clusters", "Rule 1"),
    ("eksctl create cluster", "Rule 1"),
    # .env files through Bash
    ("cat .env", "Rule 3"),
    ("source .env", "Rule 3"),
    (". ./.env", "Rule 3"),
    ("grep KEY frontend/.env.local", "Rule 3"),
    ("docker compose --env-file=.env up", "Rule 3"),
    ("docker compose --env-file .env.production up", "Rule 3"),
    ("cat .env*", "Rule 3"),
    ("cp .env .env.bak", "Rule 3"),
    ("echo x >> .env", "Rule 3"),
    ("ENV_FILE=.env make up", "Rule 3"),
    ("head -5 /workspaces/consoletrack/.env", "Rule 3"),
    # workflow and instruction files through Bash
    ("echo x > .github/workflows/test.yaml", "Rule 4"),
    ("cp docs/proposed-workflows/ci.yaml .github/workflows/", "Rule 4"),
    ("tee .github/workflows/ci.yaml < x", "Rule 4"),
    ("sed -i 's/a/b/' .github/workflows/ci.yaml", "Rule 4"),
    ("echo '@AGENTS.md' > CLAUDE.md", "instructions file"),
    ("touch .claude/CLAUDE.md", "instructions file"),
    # gh: merging and repo settings
    ("gh pr merge 1 --squash", "Rule 5"),
    ("gh -R jennasrunbooks/consoletrack pr merge 1", "Rule 5"),
    ("gh api repos/x/y/rulesets", "Rule 5"),
    ("gh secret set X", "Rule 5"),
    ("gh variable set X", "Rule 5"),
    ("gh repo edit --visibility private", "Rule 5"),
    ("gh workflow run revert.yaml", "Rule 5"),
    ("gh auth token", "Rule 3"),
    # chained commands
    ("make test && terraform apply", "Rule 1"),
    ("make test || kubectl delete pod x", "Rule 1"),
    ("ls; helm uninstall ct", "Rule 1"),
    ("ls\nterraform destroy", "Rule 1"),
    ("ls & kubectl delete pod x", "Rule 1"),
    ("(cd terraform/consoletrack/global && terraform apply)", "Rule 1"),
    ("if true; then terraform apply; fi", "Rule 1"),
    # wrappers and assignments
    ("sudo terraform apply", "Rule 1"),
    ("sudo -u root kubectl delete pod x", "Rule 1"),
    ("env terraform apply", "Rule 1"),
    ("env -i AWS_PROFILE=x aws ec2 run-instances", "Rule 1"),
    ("env -S 'terraform apply'", "Rule 1"),
    ("TF_LOG=debug terraform apply", "Rule 1"),
    ("AWS_PROFILE=x AWS_REGION=y aws s3 rm s3://b/k", "Rule 1"),
    ("command terraform apply", "Rule 1"),
    ("time terraform apply", "Rule 1"),
    ("nohup helm upgrade ct helm/consoletrack", "Rule 1"),
    ("timeout 30 kubectl delete pod x", "Rule 1"),
    ("uv run terraform apply", "Rule 1"),
    ("uv run --with boto3 aws ec2 run-instances", "Rule 1"),
    ("xargs -n1 kubectl delete pod", "Rule 1"),
    ("find . -name x -exec kubectl delete -f {} \\;", "Rule 1"),
    # nested shells and substitutions
    ("bash -c 'terraform apply'", "Rule 1"),
    ('sh -c "kubectl -n demo delete pod x"', "Rule 1"),
    ("bash -lc 'cd x && helm uninstall ct'", "Rule 1"),
    ("bash -c \"bash -c 'terraform destroy'\"", "Rule 1"),
    ("sudo bash -c 'cat .env'", "Rule 3"),
    ("eval 'terraform apply'", "Rule 1"),
    ("echo $(terraform apply -auto-approve)", "Rule 1"),
    ("echo `kubectl delete pod x`", "Rule 1"),
    ("bash <<< 'terraform apply'", "Rule 1"),
    ("bash <<'EOF'\nterraform apply\nEOF", "Rule 1"),
    ("cat <<'EOF' | sh\nkubectl delete pod x\nEOF", "Rule 1"),
    ("terraform \\\n  apply", "Rule 1"),
]


@pytest.mark.parametrize(("command", "rule"), BLOCKED)
def test_blocked(command, rule, repo):
    msg = blocked(bash(command, repo))
    assert msg is not None, f"expected {command!r} to be blocked"
    assert rule in msg


def test_block_messages_point_to_agents_md(repo):
    for command, _ in BLOCKED:
        assert "AGENTS.md" in blocked(bash(command, repo))


# ---------------------------------------------------------------- commit on main


def test_commit_on_main_blocked(main_repo):
    msg = blocked(bash("git commit -m x", main_repo))
    assert msg is not None
    assert "Rule 5" in msg and "'main'" in msg and "AGENTS.md" in msg


def test_push_on_main_blocked(main_repo):
    assert blocked(bash("git push -u origin HEAD", main_repo)) is not None


def test_commit_with_dash_c_into_main_repo_blocked(repo, main_repo):
    # cwd is a feature-branch repo, but -C points the commit at a repo on main.
    assert blocked(bash(f"git -C {main_repo} commit -m x", repo)) is not None
    assert blocked(bash(f"cd {main_repo} && git commit -m x", repo)) is not None


def test_commit_after_switching_off_main_allowed(main_repo):
    assert blocked(bash("git switch -c feat/x && git commit -m x", main_repo)) is None
    assert blocked(bash("git checkout -b feat/x && git commit -m x", main_repo)) is None


def test_checkout_of_a_file_does_not_hide_main(main_repo):
    assert blocked(bash("git checkout README.md && git commit -m x", main_repo)) is not None


def test_commit_on_feature_branch_allowed(repo):
    assert blocked(bash("git commit -m x", repo)) is None


# -------------------------------------------------------------------- file tools


@pytest.mark.parametrize("name", ["Read", "Edit", "Write", "MultiEdit"])
@pytest.mark.parametrize("path", [".env", ".env.local", "frontend/.env.production"])
def test_file_tools_block_env(name, path, repo):
    msg = blocked(tool(name, repo, file_path=path))
    assert msg is not None and "Rule 3" in msg


def test_grep_on_env_blocked(repo):
    assert blocked(tool("Grep", repo, pattern="KEY", path=".env")) is not None
    assert blocked(tool("Grep", repo, pattern="KEY", glob=".env*")) is not None
    assert blocked(tool("Grep", repo, pattern="KEY", path="backend")) is None


@pytest.mark.parametrize("name", ["Read", "Edit", "Write"])
def test_env_example_allowed(name, repo):
    assert blocked(tool(name, repo, file_path=".env.example")) is None


@pytest.mark.parametrize("name", ["Edit", "Write", "MultiEdit"])
def test_workflow_edits_blocked(name, repo):
    msg = blocked(tool(name, repo, file_path=".github/workflows/test.yaml"))
    assert msg is not None
    assert "docs/proposed-workflows/" in msg and "AGENTS.md" in msg


def test_workflow_read_allowed(repo):
    assert blocked(tool("Read", repo, file_path=".github/workflows/ci.yaml")) is None


def test_proposed_workflows_allowed(repo):
    assert blocked(tool("Write", repo, file_path="docs/proposed-workflows/ci.yaml")) is None


def test_absolute_paths_resolved_against_project(repo):
    workflow = repo / ".github" / "workflows" / "test.yaml"
    msg = blocked(tool("Write", repo, file_path=str(workflow)))
    assert msg is not None and "'.github/workflows/test.yaml'" in msg
    assert blocked(tool("Read", repo, file_path=str(repo / ".env"))) is not None
    assert blocked(tool("Write", repo, file_path=str(repo / "docs" / "x.md"))) is None


def test_path_traversal_resolved(repo):
    path = "docs/../.github/workflows/x.yaml"
    assert blocked(tool("Edit", repo, file_path=path)) is not None


@pytest.mark.parametrize(
    "path", ["CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md", "x/CLAUDE.md"]
)
def test_instruction_files_blocked(path, repo):
    msg = blocked(tool("Write", repo, file_path=path))
    assert msg is not None and "AGENTS.md" in msg
    assert blocked(tool("Edit", repo, file_path=path)) is not None


def test_instruction_files_readable(repo):
    assert blocked(tool("Read", repo, file_path="CLAUDE.md")) is None


def test_other_tools_ignored(repo):
    assert blocked(tool("WebFetch", repo, url="https://example.com/.env")) is None


# ------------------------------------------------------------- end-to-end contract


def test_hook_exit_codes(repo):
    denied = run_hook(GUARD, bash("terraform apply", repo))
    assert denied.returncode == 2
    assert "AGENTS.md" in denied.stderr
    allowed = run_hook(GUARD, bash("make test", repo))
    assert allowed.returncode == 0 and allowed.stderr == ""


def test_hook_ignores_unparseable_input():
    result = subprocess.run(
        [sys.executable, str(GUARD)], input="not json", capture_output=True, text=True
    )
    assert result.returncode == 0


def test_hook_fails_closed_on_internal_error(repo):
    payload = {"tool_name": "Bash", "tool_input": {"command": 123}}
    result = run_hook(GUARD, payload)
    assert result.returncode == 2
    assert "internal error" in result.stderr


# ------------------------------------------------------------------- lint_edited


@pytest.mark.skipif(not (shutil.which("ruff") or shutil.which("uvx")), reason="ruff not available")
def test_lint_reports_unused_import(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    bad = tmp_path / "bad.py"
    bad.write_text("import os\n")
    result = run_hook(LINT, tool("Edit", tmp_path, file_path=str(bad)))
    assert result.returncode == 2
    assert "F401" in result.stderr


@pytest.mark.skipif(not (shutil.which("ruff") or shutil.which("uvx")), reason="ruff not available")
def test_lint_clean_file_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    good = tmp_path / "good.py"
    good.write_text("print('ok')\n")
    assert run_hook(LINT, tool("Write", tmp_path, file_path=str(good))).returncode == 0


def test_lint_skips_unknown_and_missing_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "notes.md").write_text("# hi\n")
    assert run_hook(LINT, tool("Write", tmp_path, file_path="notes.md")).returncode == 0
    assert run_hook(LINT, tool("Write", tmp_path, file_path="gone.py")).returncode == 0


def test_lint_skips_frontend_without_node_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    src = tmp_path / "frontend" / "src"
    src.mkdir(parents=True)
    (src / "a.ts").write_text("const x = 1\n")
    assert run_hook(LINT, tool("Edit", tmp_path, file_path="frontend/src/a.ts")).returncode == 0


@pytest.mark.skipif(not shutil.which("terraform"), reason="terraform not installed")
def test_lint_reports_unformatted_terraform(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    tf = tmp_path / "main.tf"
    tf.write_text('variable "x" {\ndefault = 1\n}\n')
    result = run_hook(LINT, tool("Write", tmp_path, file_path="main.tf"))
    assert result.returncode == 2
    assert tf.read_text() == 'variable "x" {\ndefault = 1\n}\n', "hook must not modify files"
