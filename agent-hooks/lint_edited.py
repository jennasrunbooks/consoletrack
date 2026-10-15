#!/usr/bin/env python3
"""PostToolUse hook: lint the file an Edit/Write/MultiEdit just touched.

Problems go back to the agent (exit 2 + stderr) so it fixes them in the same turn:

- ``.py``                            -> ``ruff check`` (``uvx ruff`` when ruff isn't on PATH)
- ``.ts/.tsx/.js/.jsx`` in frontend/ -> ``npx --no-install eslint``, once node_modules exists
- ``.tf/.tfvars``                    -> ``terraform fmt -check -diff``

A linter that isn't installed yet is skipped silently. The hook only reports; it never
modifies files. Standard library only, like guard.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}
TIMEOUT = 50  # seconds; the hook itself has a 60s timeout in .claude/settings.json


def linter_for(path: Path, project: Path) -> tuple[list[str], Path, set[int]] | None:
    """Return (command, cwd, exit codes that mean "found problems"), or None to skip."""
    suffix = path.suffix
    if suffix == ".py":
        if shutil.which("ruff"):
            return ["ruff", "check", str(path)], project, {1}
        if shutil.which("uvx"):
            return ["uvx", "ruff", "check", str(path)], project, {1}
        return None
    if suffix in {".ts", ".tsx", ".js", ".jsx"}:
        frontend = project / "frontend"
        if not path.is_relative_to(frontend) or not (frontend / "node_modules").is_dir():
            return None
        if not shutil.which("npx"):
            return None
        return ["npx", "--no-install", "eslint", str(path)], frontend, {1}
    if suffix in {".tf", ".tfvars"}:
        if not shutil.which("terraform"):
            return None
        # 3 = needs formatting, 2 = doesn't parse
        return ["terraform", "fmt", "-check", "-diff", str(path)], project, {2, 3}
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if payload.get("tool_name") not in EDIT_TOOLS:
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path:
        return 0

    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    path = Path(payload.get("cwd") or project, file_path).resolve()
    if not path.is_file():
        return 0
    linter = linter_for(path, project.resolve())
    if linter is None:
        return 0
    cmd, cwd, problem_codes = linter

    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if result.returncode not in problem_codes:
        return 0  # clean, or the linter itself failed (e.g. uvx offline): don't block

    output = (result.stdout + result.stderr).strip()
    print(
        f"{cmd[0]} {' '.join(cmd[1:-1])} found problems in {file_path}:\n{output}", file=sys.stderr
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
