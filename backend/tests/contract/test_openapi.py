"""FastAPI's generated schema matches `openapi.yaml`, the source of truth.

Compared for every operation: operationId, tags, parameters, request body, response status
codes and their schemas, effective security, and `x-` extensions. Compared for every component
schema and security scheme: the validation keywords.

Normalization (so only real contract differences fail):

- Annotations are ignored: title, description, summary, examples, and `default: null`.
- `type: [X, "null"]` is the same as `anyOf: [{type: X}, {type: "null"}]` (Pydantic's form).
- An optional property, parameter, or request body may be nullable or not: "absent" and
  "null" mean the same thing for those.
- `$ref`s to `components/parameters` and `components/responses` are inlined.
- Ordering of `required`, `enum`, `anyOf`, parameters, and tags doesn't matter.
- Security is the effective requirement: the operation's own, else the document default.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.main import create_app
from app.settings import Settings
from tests.unit.fakes import FakeStore

SPEC_PATH = Path(__file__).resolve().parents[3] / "openapi.yaml"
METHODS = ("get", "put", "post", "delete", "patch")
ANNOTATIONS = {"title", "description", "summary", "examples", "example"}
NULL = {"type": "null"}


def _sorted(items: list[Any]) -> list[Any]:
    return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))


def unwrap_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    branches = schema.get("anyOf")
    if isinstance(branches, list) and len(branches) == 2 and NULL in branches:
        (other,) = [branch for branch in branches if branch != NULL]
        rest = {key: value for key, value in schema.items() if key != "anyOf"}
        return {**rest, **other}
    return schema


def norm_schema(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    schema = {key: value for key, value in schema.items() if key not in ANNOTATIONS}
    if "default" in schema and schema["default"] is None:
        del schema["default"]

    types = schema.get("type")
    if isinstance(types, list) and "null" in types:
        others = [t for t in types if t != "null"]
        base = {key: value for key, value in schema.items() if key != "type"}
        base["type"] = others[0] if len(others) == 1 else others
        schema = {"anyOf": [base, NULL]}

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "properties":
            out[key] = {name: norm_schema(prop) for name, prop in value.items()}
        elif key in ("items", "additionalProperties", "propertyNames", "not"):
            out[key] = norm_schema(value)
        elif key in ("anyOf", "oneOf", "allOf"):
            out[key] = _sorted([norm_schema(branch) for branch in value])
        elif key in ("required", "enum"):
            out[key] = sorted(value)
        else:
            out[key] = value
    if "properties" in out:
        # Optional properties: "absent" and "null" are the same contract.
        required = set(out.get("required", []))
        out["properties"] = {
            name: prop if name in required else unwrap_nullable(prop)
            for name, prop in out["properties"].items()
        }
    if len(out.get("anyOf", [])) == 1:
        (only,) = out.pop("anyOf")
        out.update(only)
    return out


def _resolve(doc: dict[str, Any], node: Any) -> Any:
    ref = node.get("$ref") if isinstance(node, dict) else None
    if ref and not ref.startswith("#/components/schemas/"):
        target: Any = doc
        for part in ref.removeprefix("#/").split("/"):
            target = target[part]
        return _resolve(doc, target)
    return node


def _content(doc: dict[str, Any], content: dict[str, Any] | None, optional: bool) -> Any:
    out = {}
    for media_type, body in (content or {}).items():
        schema = norm_schema(body.get("schema", {}))
        out[media_type] = unwrap_nullable(schema) if optional else schema
    return out


def norm_operation(doc: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    parameters = []
    for raw in op.get("parameters", []):
        param = _resolve(doc, raw)
        required = bool(param.get("required", False))
        schema = norm_schema(param.get("schema", {}))
        parameters.append(
            {
                "name": param["name"],
                "in": param["in"],
                "required": required,
                "schema": schema if required else unwrap_nullable(schema),
            }
        )
    body = op.get("requestBody")
    request_body = None
    if body is not None:
        required = bool(body.get("required", False))
        request_body = {
            "required": required,
            "content": _content(doc, body.get("content"), optional=not required),
        }
    responses = {
        str(code): _content(doc, _resolve(doc, response).get("content"), optional=False)
        for code, response in op.get("responses", {}).items()
    }
    security = op.get("security", doc.get("security", []))
    return {
        "operationId": op.get("operationId"),
        "tags": sorted(op.get("tags", [])),
        "parameters": sorted(parameters, key=lambda p: (p["in"], p["name"])),
        "requestBody": request_body,
        "responses": responses,
        "security": _sorted(
            [{name: sorted(scopes) for name, scopes in req.items()} for req in security]
        ),
        "extensions": {key: value for key, value in op.items() if key.startswith("x-")},
    }


def norm_document(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    components = doc.get("components", {})
    return {
        "operations": {
            f"{method.upper()} {path}": norm_operation(doc, item[method])
            for path, item in doc.get("paths", {}).items()
            for method in METHODS
            if method in item
        },
        "schemas": {
            name: norm_schema(schema) for name, schema in components.get("schemas", {}).items()
        },
        "securitySchemes": {
            name: {key: value for key, value in scheme.items() if key not in ANNOTATIONS}
            for name, scheme in components.get("securitySchemes", {}).items()
        },
    }


def diff(expected: Any, actual: Any) -> str:
    def dump(value: Any) -> list[str]:
        return json.dumps(value, indent=2, sort_keys=True).splitlines()

    return "\n".join(
        difflib.unified_diff(
            dump(expected), dump(actual), "openapi.yaml", "app.openapi()", lineterm=""
        )
    )


SPEC = norm_document(yaml.safe_load(SPEC_PATH.read_text()))
APP = norm_document(
    create_app(Settings(), uow_factory=FakeStore().uow, db_check=lambda: None).openapi()
)


@pytest.mark.parametrize("section", ["operations", "schemas", "securitySchemes"])
def test_same_names(section: str) -> None:
    expected, actual = sorted(SPEC[section]), sorted(APP[section])
    assert expected == actual, (
        f"{section} differ.\n  only in openapi.yaml: {sorted(set(expected) - set(actual))}"
        f"\n  only in app.openapi(): {sorted(set(actual) - set(expected))}"
    )


def _cases(section: str) -> list[str]:
    return sorted(set(SPEC[section]) & set(APP[section]))


@pytest.mark.parametrize("operation", _cases("operations"))
def test_operation_matches(operation: str) -> None:
    expected, actual = SPEC["operations"][operation], APP["operations"][operation]
    assert expected == actual, f"{operation} differs:\n{diff(expected, actual)}"


@pytest.mark.parametrize("schema", _cases("schemas"))
def test_schema_matches(schema: str) -> None:
    expected, actual = SPEC["schemas"][schema], APP["schemas"][schema]
    assert expected == actual, f"schema {schema} differs:\n{diff(expected, actual)}"


@pytest.mark.parametrize("scheme", _cases("securitySchemes"))
def test_security_scheme_matches(scheme: str) -> None:
    expected, actual = SPEC["securitySchemes"][scheme], APP["securitySchemes"][scheme]
    assert expected == actual, f"security scheme {scheme} differs:\n{diff(expected, actual)}"
