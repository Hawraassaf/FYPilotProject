"""
Generic Pydantic schema validation shared by every stage of the review
pipeline.

This module keeps the original ``validate() -> (bool, dict)`` API so callers
such as ``guarded_call`` remain backward compatible, while also exposing
``validate_detailed()`` for the structural-repair path.  The detailed result
contains the exact Pydantic errors and the expected JSON schema, allowing the
repair model to fix the real problem instead of guessing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class SchemaValidationResult:
    valid: bool
    data: dict[str, Any]
    errors: list[dict[str, Any]] = field(default_factory=list)
    expected_schema: dict[str, Any] = field(default_factory=dict)


def _json_safe(value: Any) -> Any:
    """Convert arbitrary Pydantic error values into JSON-safe data."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _format_location(location: tuple[Any, ...]) -> str:
    if not location:
        return "$"

    parts: list[str] = []
    for item in location:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            if parts:
                parts.append(".")
            parts.append(str(item))
    return "".join(parts)


def _compact_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """
    Keep the information a repair model needs without dumping huge candidate
    fragments back into the prompt.
    """
    compact: list[dict[str, Any]] = []

    for error in exc.errors(include_url=False):
        item: dict[str, Any] = {
            "location": _format_location(tuple(error.get("loc") or ())),
            "message": str(error.get("msg") or "Schema validation failed."),
            "type": str(error.get("type") or "validation_error"),
        }

        if "ctx" in error and error["ctx"]:
            item["context"] = _json_safe(error["ctx"])

        compact.append(item)

    return compact


def validate_detailed(
    schema: type[ModelT],
    candidate: Any,
) -> SchemaValidationResult:
    """
    Validate and return exact repair guidance.

    On success, ``data`` is the schema-coerced dictionary with defaults filled.
    On failure, ``data`` preserves the original candidate (or wraps a non-dict
    candidate under ``_raw``), ``errors`` contains compact Pydantic errors, and
    ``expected_schema`` contains the model's JSON schema.
    """
    expected_schema = schema.model_json_schema()

    if not isinstance(candidate, dict):
        return SchemaValidationResult(
            valid=False,
            data={"_raw": candidate},
            errors=[
                {
                    "location": "$",
                    "message": "The provider output must be a JSON object.",
                    "type": "object_type",
                }
            ],
            expected_schema=expected_schema,
        )

    try:
        validated = schema.model_validate(candidate)
    except ValidationError as exc:
        return SchemaValidationResult(
            valid=False,
            data=candidate,
            errors=_compact_validation_errors(exc),
            expected_schema=expected_schema,
        )

    return SchemaValidationResult(
        valid=True,
        data=validated.model_dump(),
        errors=[],
        expected_schema=expected_schema,
    )


def validate(schema: type[ModelT], candidate: Any) -> tuple[bool, dict]:
    """
    Backward-compatible wrapper used by existing firewall/guard code.
    """
    result = validate_detailed(schema, candidate)
    return result.valid, result.data