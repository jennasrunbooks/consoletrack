"""The `submit_analysis` tool: the agent's only way to finish a run, and the structured result.

Versioned: `SCHEMA_VERSION` changes with any field change, so evals and stored analyses can tell
which shape produced them. The model's input is validated here (Pydantic) before anything is
stored; an invalid call goes back to the model as a tool error, never into the database.

The proposal kinds are deliberately narrow. Each names a Deployment by namespace and name and
carries only the values the fix needs, so there is no free-form patch for a prompt injection to
fill in. The deterministic validator and the policy gate (#34, `app/services/policy_gate.py`)
check the proposal again before anyone sees it.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.enums import AnalysisFlag, Confidence, Risk

SCHEMA_VERSION = "1"
TOOL_NAME = "submit_analysis"

# RFC 1123 names (namespaces, Deployments, containers), as k8s-mcp checks them.
K8S_NAME = r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$"
# A memory quantity in Mi or Gi: the only units the validator compares.
MEMORY_QUANTITY = r"^[1-9][0-9]{0,5}(Mi|Gi)$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Name = Annotated[str, Field(pattern=K8S_NAME, max_length=253)]
Reason = Annotated[str, Field(min_length=1, max_length=2000)]


class SetMemoryLimit(_Strict):
    """Raise one container's memory limit after an OOM kill (the request follows if needed)."""

    kind: Literal["set_memory_limit"]
    namespace: Name
    deployment: Name
    container: Name
    memory_limit: Annotated[
        str, Field(pattern=MEMORY_QUANTITY, description="New limit, e.g. 256Mi")
    ]
    reason: Reason


class Rollback(_Strict):
    """Roll a Deployment back to an earlier revision (rollout undo), e.g. after a bad image."""

    kind: Literal["rollback"]
    namespace: Name
    deployment: Name
    to_revision: Annotated[
        int | None, Field(ge=1, description="Revision to return to; null = the previous one")
    ]
    reason: Reason


class RevertWorkflow(_Strict):
    """Revert a linked out-of-band AWS change through the gated Terraform workflow."""

    kind: Literal["revert_workflow"]
    change_id: Annotated[int, Field(ge=1)]
    reason: Reason


Proposal = Annotated[SetMemoryLimit | Rollback | RevertWorkflow, Field(discriminator="kind")]


class SubmitAnalysis(_Strict):
    summary: Annotated[
        str,
        Field(
            min_length=1,
            max_length=4000,
            description="Plain-language who, what, when, and how. No Markdown.",
        ),
    ]
    title: Annotated[
        str | None,
        Field(max_length=200, description="Changes only: a one-line title; null for incidents"),
    ] = None
    root_cause: Annotated[
        str | None,
        Field(max_length=4000, description="Incidents: the cause; null for a change"),
    ]
    # Descriptions and a required risk_reason keep the explanation out of the enum: without
    # them Haiku wrote "High. The only replica..." into risk.
    risk: Annotated[
        Risk, Field(description="One word: low, medium, or high. Explain in risk_reason")
    ]
    risk_reason: Annotated[
        str | None,
        Field(max_length=2000, description="Why that risk: the impact if nothing is done"),
    ]
    confidence: Annotated[Confidence, Field(description="One word: low, medium, or high")]
    linked_change_ids: Annotated[
        list[Annotated[int, Field(ge=1)]],
        Field(max_length=10, description="IDs of ConsoleTrack changes that likely caused this"),
    ]
    proposed_remediation: Proposal | None
    flags: Annotated[list[AnalysisFlag], Field(max_length=len(AnalysisFlag))]
    postmortem_md: Annotated[
        str | None, Field(max_length=20_000, description="Incidents: a draft postmortem")
    ] = None


TOOL_DESCRIPTION = """\
Submit your analysis. This is the only way to finish: call it exactly once, when you have \
enough evidence or when more tool calls would not help. The fields are stored as the record a \
human reviews. proposed_remediation is only a proposal: a human or the policy gate decides, and \
null is the right answer when no narrow, safe fix exists."""


# Strict tool use: the API constrains the model's input to the schema, so a call can't leave
# out a required field. Without it Haiku dropped confidence, risk, or root_cause and needed up to
# five retries (6x the tokens). Strict schemas take no length, numeric, or maxItems bounds and no
# oneOf, so those are moved into the description; SubmitAnalysis still enforces them.
UNSUPPORTED_IN_STRICT = (
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "maxItems",
)


def tool_definition() -> dict[str, Any]:
    """The tool as the Messages API takes it: `$ref`s inlined and strict."""
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "strict": True,
        "input_schema": _strict(_inline(SubmitAnalysis.model_json_schema())),
    }


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                # Keep the field's own keys (its description) next to the inlined definition.
                siblings = {key: value for key, value in node.items() if key != "$ref"}
                return walk({**defs[node["$ref"].rsplit("/", 1)[-1]], **siblings})
            return {
                key: walk(value)
                for key, value in node.items()
                if key not in ("title", "discriminator")
            }
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(schema)


def _strict(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _strict(value) for key, value in node.items() if key not in UNSUPPORTED_IN_STRICT}
    if "oneOf" in out:
        out["anyOf"] = out.pop("oneOf")
    bounds = [f"{key} {node[key]}" for key in UNSUPPORTED_IN_STRICT if key in node]
    if out.get("minItems", 0) > 1:  # strict takes only 0 or 1
        bounds.insert(0, f"minItems {out.pop('minItems')}")
    if bounds:
        note = f"({', '.join(bounds)})"
        out["description"] = f"{out['description']} {note}" if "description" in out else note
    return out
