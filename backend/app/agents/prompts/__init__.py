"""Versioned prompt files. A wording change is a new `_vN` file, never an edit in place, so an
analysis can be traced to the prompt that produced it: every agent run records `PROMPT_VERSION`.
The three prompts move together, so a version names the whole set.

- v1: the first agent loop (#10).
- v2: reading container restarts for the OOM scenario (#60): exit 137 soon after start without a
  liveness failure is a likely OOM, every container's last termination, working set can miss a
  short peak, a probe diagnosis needs the kubelet's events, `superseded` rollout revisions.
- v3: after an OOM, whether the memory limit changed in a recent revision decides the fix:
  lowered → `rollback`, unchanged → `set_memory_limit` (#60). v2's OOM fault lowered the limit in a
  new revision, so `rollback` was a fair answer to a scenario that expected a limit raise.
- v4: v3 plus one rule (#79): a rollout revision with `remediation` set (k8s-mcp's
  get_rollout_history) was made by ConsoleTrack's own remediation, its fix or its rollback: not a
  human change, and never a revision to roll back to. Under v3, triage read the apply loop's
  rollback as a human lowering the limit and proposed rolling back to the fix that had OOMed.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from string import Template

PROMPT_VERSION = "v4"


@cache
def load(name: str, version: str = PROMPT_VERSION) -> str:
    return files(__package__).joinpath(f"{name}_{version}.md").read_text(encoding="utf-8")


def render(name: str, version: str = PROMPT_VERSION, **values: object) -> str:
    """The prompt with `$name` placeholders filled; a missing value raises `KeyError`."""
    return Template(load(name, version)).substitute(values)
