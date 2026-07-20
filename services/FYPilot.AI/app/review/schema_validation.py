"""
Generic Pydantic schema coercion, shared by every stage of the review
pipeline (the Writer/Rewrite candidate against the agent's own response
model, and the Reviewer's own findings against ReviewerFindings).

An unrecoverable failure here is reported as schema_invalid — a distinct,
honest terminal status separate from "rejected" (which means the Reviewer
judged the *content* unacceptable, not that the object never parsed).
"""

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


def validate(schema: type[ModelT], candidate: Any) -> tuple[bool, dict]:
    """
    Returns (schema_ok, data). On success, data is the schema-coerced dict
    (defaults filled, types coerced). On failure, data is the original
    candidate unchanged (dict) so callers can still inspect what came back,
    but schema_ok=False signals it must not be trusted as-is.
    """
    if not isinstance(candidate, dict):
        return False, {"_raw": candidate}

    try:
        validated = schema.model_validate(candidate)
    except ValidationError:
        return False, candidate

    return True, validated.model_dump()
