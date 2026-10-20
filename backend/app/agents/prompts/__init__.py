"""Versioned prompt files. A wording change is a new `_vN` file, never an edit in place, so an
analysis can be traced to the prompt that produced it: every agent run records `PROMPT_VERSION`.
The three prompts move together, so a version names the whole set.

- v1: the first agent loop (#10).
- v2: reading container restarts for the OOM scenario (#60): exit 137 soon after start without a
  liveness failure is a likely OOM, every container's last termination, working set can miss a
  short peak, a probe diagnosis needs the kubelet's events, `superseded` rollout revisions.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from string import Template

PROMPT_VERSION = "v2"


@cache
def load(name: str, version: str = PROMPT_VERSION) -> str:
    return files(__package__).joinpath(f"{name}_{version}.md").read_text(encoding="utf-8")


def render(name: str, version: str = PROMPT_VERSION, **values: object) -> str:
    """The prompt with `$name` placeholders filled; a missing value raises `KeyError`."""
    return Template(load(name, version)).substitute(values)
