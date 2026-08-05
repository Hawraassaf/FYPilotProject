from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.review.rewrite_agent import RewriteAgent
from app.review.schema_validation import validate, validate_detailed


class NestedItem(BaseModel):
    id: str
    priority: Literal["high", "medium", "low"]


class ExampleCandidate(BaseModel):
    title: str
    score: int = Field(ge=0, le=100)
    items: list[NestedItem]


class FakeProviderChain:
    def __init__(self) -> None:
        self.last_prompt = ""

    def generate_json(self, prompt: str, *, use_search: bool = False):
        self.last_prompt = prompt
        return {"prompt": prompt, "use_search": use_search}


class RecordingProviderChain:
    """Accepts the full generate_json signature so max_tokens/estimated
    token kwargs actually reach it, unlike FakeProviderChain above (which
    exists specifically to prove the graceful fallback for minimal fakes)."""

    def __init__(self) -> None:
        self.last_kwargs: dict = {}

    def generate_json(self, prompt, **kwargs):
        self.last_kwargs = kwargs
        return {"prompt": prompt, **kwargs}


def test_validate_detailed_returns_exact_errors_and_expected_schema() -> None:
    candidate = {
        "title": "Demo",
        "score": 120,
        "items": [{"id": "FR-01", "priority": "urgent"}],
    }

    result = validate_detailed(ExampleCandidate, candidate)

    assert result.valid is False
    assert result.data == candidate
    assert result.expected_schema["title"] == "ExampleCandidate"

    locations = {error["location"] for error in result.errors}
    assert "score" in locations
    assert "items[0].priority" in locations

    error_types = {error["type"] for error in result.errors}
    assert "less_than_equal" in error_types
    assert "literal_error" in error_types


def test_validate_keeps_backward_compatible_tuple_api() -> None:
    valid, data = validate(
        ExampleCandidate,
        {
            "title": "Demo",
            "score": 80,
            "items": [{"id": "FR-01", "priority": "high"}],
        },
    )

    assert valid is True
    assert data["score"] == 80
    assert data["items"][0]["priority"] == "high"


def test_non_dict_candidate_reports_root_object_error() -> None:
    result = validate_detailed(ExampleCandidate, ["not", "an", "object"])

    assert result.valid is False
    assert result.data == {"_raw": ["not", "an", "object"]}
    assert result.errors[0]["location"] == "$"
    assert result.errors[0]["type"] == "object_type"


def test_structural_repair_prompt_contains_candidate_errors_and_schema() -> None:
    fake_chain = FakeProviderChain()
    agent = RewriteAgent(fake_chain)  # type: ignore[arg-type]

    candidate = {"title": "Demo", "score": 120, "items": []}
    validation = validate_detailed(ExampleCandidate, candidate)

    result = agent.fix_structure(
        validation.data,
        agent_name="ExampleAgent",
        validation_errors=validation.errors,
        expected_schema=validation.expected_schema,
    )

    prompt = fake_chain.last_prompt
    assert result["use_search"] is False
    assert "EXACT VALIDATION ERRORS" in prompt
    assert "EXPECTED JSON SCHEMA" in prompt
    assert '"location": "score"' in prompt
    assert '"maximum":100' in prompt
    assert "Correct every validation error listed above" in prompt


# ---------------------------------------------------------------------------
# Regression: fix_structure()/rewrite() must size max_tokens from the actual
# candidate instead of silently defaulting to 2200 (see
# DeepInfraProvider/GroqProvider's `effective_max_tokens = max_tokens or
# 2200`). A "return the COMPLETE object" repair/rewrite request for a large
# candidate always truncated under the old default -- observed live
# 2026-08-04 (repeated `is_truncated=True` from every provider on SE
# Documentation's structural-repair calls, unrelated to timeout or model
# choice).
# ---------------------------------------------------------------------------

def test_fix_structure_uses_floor_max_tokens_for_a_small_candidate() -> None:
    chain = RecordingProviderChain()
    agent = RewriteAgent(chain)  # type: ignore[arg-type]

    candidate = {"title": "Demo", "score": 120, "items": []}
    validation = validate_detailed(ExampleCandidate, candidate)

    agent.fix_structure(
        validation.data, agent_name="ExampleAgent",
        validation_errors=validation.errors, expected_schema=validation.expected_schema,
    )

    assert chain.last_kwargs["max_tokens"] == 2200


def test_fix_structure_scales_max_tokens_up_for_a_large_candidate() -> None:
    chain = RecordingProviderChain()
    agent = RewriteAgent(chain)  # type: ignore[arg-type]

    large_candidate = {
        "title": "Demo",
        "score": 80,
        "items": [{"id": f"FR-{i}", "priority": "high", "detail": "x" * 200} for i in range(200)],
    }
    validation = validate_detailed(ExampleCandidate, large_candidate)

    agent.fix_structure(
        validation.data, agent_name="ExampleAgent",
        validation_errors=validation.errors, expected_schema=validation.expected_schema,
    )

    assert chain.last_kwargs["max_tokens"] > 2200
    assert chain.last_kwargs["max_tokens"] <= 8000


def test_fix_structure_passes_estimated_prompt_tokens_and_groq_limit() -> None:
    chain = RecordingProviderChain()
    agent = RewriteAgent(chain)  # type: ignore[arg-type]

    candidate = {"title": "Demo", "score": 80, "items": []}
    validation = validate_detailed(ExampleCandidate, candidate)

    agent.fix_structure(
        validation.data, agent_name="ExampleAgent",
        validation_errors=validation.errors, expected_schema=validation.expected_schema,
    )

    assert chain.last_kwargs["estimated_prompt_tokens"] > 0
    assert chain.last_kwargs["provider_token_limits"] == {"groq": 12000}


def test_fix_structure_falls_back_gracefully_for_a_minimal_test_double() -> None:
    # FakeProviderChain only accepts (prompt, *, use_search=False) -- proves
    # the new max_tokens/estimated_prompt_tokens kwargs never break an old,
    # minimal provider_chain test double instead of erroring outright.
    fake_chain = FakeProviderChain()
    agent = RewriteAgent(fake_chain)  # type: ignore[arg-type]

    candidate = {"title": "Demo", "score": 120, "items": []}
    validation = validate_detailed(ExampleCandidate, candidate)

    result = agent.fix_structure(
        validation.data, agent_name="ExampleAgent",
        validation_errors=validation.errors, expected_schema=validation.expected_schema,
    )

    assert result["use_search"] is False
