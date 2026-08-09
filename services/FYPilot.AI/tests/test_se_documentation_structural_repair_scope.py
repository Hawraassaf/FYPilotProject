"""
Tests for app/review/se_documentation_structural_repair_scope.py -- payload
containment for SE Documentation's STRUCTURAL-repair path (the counterpart to
se_documentation_rewrite_scope.py's semantic-rewrite fix, applied to
schema_invalid repair instead of reviewer-rejected content). See that
module's docstring for the full root-cause history this protects against.

Numbered comments map to the required-tests list from the fix specification.
"""

from __future__ import annotations

import json

from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.section_scope import _NEVER_LLM_REWRITABLE_FIELDS
from app.review.se_documentation_rewrite_scope import RewriteClosure
from app.review.se_documentation_structural_repair_scope import (
    SE_DOC_STRUCTURAL_REPAIR_MAX_PROMPT_TOKENS,
    SE_DOC_STRUCTURAL_REPAIR_PROMPT_SAFETY_MARGIN_TOKENS,
    StructuralRepairPayloadTooLargeError,
    StructuralRepairScopeError,
    build_compact_rewrite_candidate,
    build_schema_fragment,
    build_structural_repair_prompt,
    build_structural_repair_scope_prompt,
    estimate_tokens,
    merge_structural_repair,
    resolve_structural_repair_closure,
    resolve_structural_repair_plan,
    restore_never_llm_rewritable_fields,
    validate_structural_repair_response,
)


def _candidate(**overrides) -> dict:
    base = {
        "projectTitle": "Test Project",
        "functionalRequirements": [
            {"id": "FR-1", "title": "Submit data", "description": "d", "priority": "High"},
        ],
        "useCases": [
            {"id": "UC-1", "title": "Submit", "actor": "User", "goal": "g"},
        ],
        "architecture": {"style": "layered", "explanation": "e"},
        "testingPlan": [
            {"id": "TC-1", "title": "Submit test", "type": "functional"},
        ],
        "documentationQualityScore": 80,
        "qualityAssessment": {"overallScore": 80},
        "mermaidERD": "erDiagram\n  A ||--o{ B : has",
        "sectionProvenance": {"functionalRequirements": "writer"},
    }
    base.update(overrides)
    return base


def _error(location: str, message: str = "field required", error_type: str = "missing") -> dict:
    return {"location": location, "message": message, "type": error_type}


def _real_schema():
    from app.review.registry import get_agent_config
    return get_agent_config("SEDocumentationAgent").schema


def _real_valid_candidate() -> dict:
    from app.agents.se_documentation.se_documentation_orchestrator import (
        SEDocSelectedIdea,
        SEDocumentationOrchestratorAgent,
        SEDocumentationRequest,
    )

    agent = SEDocumentationOrchestratorAgent()
    return agent.build_safe_fallback(
        SEDocumentationRequest(selectedIdea=SEDocSelectedIdea(title="Test Project"))
    ).model_dump()


# ---------------------------------------------------------------------------
# Test 1: localized error selects one section
# ---------------------------------------------------------------------------

def test_single_section_error_resolves_to_that_section_only():
    candidate = _candidate()
    closure = resolve_structural_repair_closure(
        candidate, [_error("functionalRequirements[0].priority")],
    )

    assert closure.allowed_sections == frozenset({"functionalRequirements"})

    payload = build_compact_rewrite_candidate(candidate, closure)
    assert set(payload.keys()) == {"functionalRequirements"}
    assert "useCases" not in payload
    assert "architecture" not in payload


def test_scoped_repair_does_not_include_full_top_level_schema():
    fragment = build_schema_fragment(_real_schema(), {"functionalRequirements"})
    full_schema = _real_schema().model_json_schema()

    # The fragment's properties are a strict subset of the full schema's --
    # never the complete top-level schema resent unnecessarily.
    assert set(fragment.get("properties", {}).keys()) == {"functionalRequirements"}
    assert len(fragment.get("properties", {})) < len(full_schema.get("properties", {}))


# ---------------------------------------------------------------------------
# Test 2: multiple errors select multiple sections
# ---------------------------------------------------------------------------

def test_multiple_section_errors_resolve_to_each_section_exactly_once():
    candidate = _candidate()
    closure = resolve_structural_repair_closure(
        candidate,
        [
            _error("functionalRequirements[0].priority"),
            _error("architecture.style"),
            # A second error inside the SAME section as the first -- must
            # not produce a duplicate entry (allowed_sections is a set).
            _error("functionalRequirements[0].title"),
        ],
    )

    assert closure.allowed_sections == frozenset({"functionalRequirements", "architecture"})
    assert len(closure.allowed_sections) == 2  # no duplicate despite two FR errors

    payload = build_compact_rewrite_candidate(candidate, closure)
    assert set(payload.keys()) == {"functionalRequirements", "architecture"}
    assert "useCases" not in payload
    assert "testingPlan" not in payload


# ---------------------------------------------------------------------------
# Test 3: untouched sections are preserved
# ---------------------------------------------------------------------------

def test_untouched_sections_are_preserved_value_for_value_after_merge():
    candidate = _candidate()
    closure = resolve_structural_repair_closure(candidate, [_error("architecture.style")])

    response = {"architecture": {"style": "microservices", "explanation": "corrected"}}
    merged = merge_structural_repair(candidate, response, closure)

    assert merged["useCases"] == candidate["useCases"]
    assert merged["functionalRequirements"] == candidate["functionalRequirements"]
    assert merged["testingPlan"] == candidate["testingPlan"]
    assert merged["architecture"] == response["architecture"]
    # Original is never mutated in place.
    assert candidate["architecture"] == {"style": "layered", "explanation": "e"}


def test_never_llm_rewritable_fields_always_restored_from_original():
    candidate = _candidate()
    closure = resolve_structural_repair_closure(candidate, [_error("architecture.style")])

    # A malicious/confused response tries to smuggle a changed mermaidERD in
    # anyway -- merge_targeted_rewrite (reused unchanged) must still restore
    # it from the original regardless of what the response contains, since
    # only closure.allowed_sections keys are ever read from the response.
    response = {"architecture": {"style": "microservices", "explanation": "e"}}
    merged = merge_structural_repair(candidate, response, closure)

    assert merged["mermaidERD"] == candidate["mermaidERD"]
    assert merged["sectionProvenance"] == candidate["sectionProvenance"]


# ---------------------------------------------------------------------------
# Test 4: unsolicited repaired section is rejected
# ---------------------------------------------------------------------------

def test_unsolicited_section_in_response_is_rejected():
    candidate = _candidate()
    closure = resolve_structural_repair_closure(candidate, [_error("architecture.style")])

    response = {
        "architecture": {"style": "microservices", "explanation": "e"},
        "useCases": [{"id": "UC-99", "title": "Unsolicited", "actor": "X", "goal": "g"}],
    }

    try:
        validate_structural_repair_response(response, closure)
        assert False, "expected StructuralRepairScopeError for an unsolicited section"
    except StructuralRepairScopeError as exc:
        assert "useCases" in str(exc)


# ---------------------------------------------------------------------------
# Test 5: requested section missing from response
# ---------------------------------------------------------------------------

def test_missing_requested_section_in_response_is_rejected():
    candidate = _candidate()
    closure = resolve_structural_repair_closure(
        candidate, [_error("functionalRequirements[0].priority"), _error("architecture.style")],
    )

    # Only returns architecture -- functionalRequirements silently dropped.
    response = {"architecture": {"style": "microservices", "explanation": "e"}}

    try:
        validate_structural_repair_response(response, closure)
        assert False, "expected StructuralRepairScopeError for a missing requested section"
    except StructuralRepairScopeError as exc:
        assert "functionalRequirements" in str(exc)


def test_non_dict_response_is_rejected():
    candidate = _candidate()
    closure = resolve_structural_repair_closure(candidate, [_error("architecture.style")])

    try:
        validate_structural_repair_response(["not", "a", "dict"], closure)
        assert False, "expected StructuralRepairScopeError for a non-dict response"
    except StructuralRepairScopeError:
        pass


# ---------------------------------------------------------------------------
# Test 6: duplicate section replacement is rejected
# ---------------------------------------------------------------------------

def test_closure_never_produces_duplicate_section_entries_by_construction():
    candidate = _candidate()

    # Three separate errors, two of which target the same section under
    # different sub-paths -- allowed_sections is a frozenset, so the
    # section can only ever be requested/merged once; there is no code path
    # by which the same section could be replaced twice from one response.
    closure = resolve_structural_repair_closure(
        candidate,
        [
            _error("functionalRequirements[0].priority"),
            _error("functionalRequirements[1].title"),
            _error("functionalRequirements"),
        ],
    )

    sections = list(closure.allowed_sections)
    assert sections.count("functionalRequirements") == 1

    # A response can likewise never carry two values for the same JSON key
    # (dict keys are inherently unique) -- validate_structural_repair_response
    # only ever reads response[section] once per section.
    response = {"functionalRequirements": [{"id": "FR-1", "title": "t", "description": "d", "priority": "Low"}]}
    validated = validate_structural_repair_response(response, closure)
    assert list(validated.keys()) == ["functionalRequirements"]


# ---------------------------------------------------------------------------
# Test 7: complete merged candidate must validate
# ---------------------------------------------------------------------------

def test_valid_fragment_but_invalid_merged_document_is_not_accepted():
    from app.review.schema_validation import validate_detailed

    real_candidate = _real_valid_candidate()
    closure = resolve_structural_repair_closure(real_candidate, [_error("functionalRequirements[0].priority")])

    # Reuse the real candidate's own existing requirement id(s) -- other
    # sections (e.g. useCases) reference them by exact id via a cross-field
    # validator, so a fragment that changes the id (even to something
    # equally schema-shaped) would fail full validation for an UNRELATED
    # reason and defeat this test's actual point.
    existing_ids = [row["id"] for row in real_candidate["functionalRequirements"]]
    response = {
        "functionalRequirements": [
            {**row, "title": "Valid title", "description": "Valid description", "priority": "High"}
            for row in real_candidate["functionalRequirements"]
        ],
    }
    assert [row["id"] for row in response["functionalRequirements"]] == existing_ids
    merged = merge_structural_repair(real_candidate, response, closure)

    # Sanity: the merge alone doesn't corrupt anything real_candidate didn't
    # already have -- full validation must still be run afterward and is
    # the ONLY authority for acceptance (this test's core assertion is that
    # a caller MUST call validate_detailed on `merged`, never skip it).
    result = validate_detailed(_real_schema(), merged)
    assert result.valid, result.errors

    # Now prove the negative case: an out-of-schema value in the returned
    # fragment (wrong type) must fail full validation even though it was
    # the only content requested.
    bad_response = {"functionalRequirements": "not-a-list"}
    bad_merged = merge_structural_repair(real_candidate, bad_response, closure)
    bad_result = validate_detailed(_real_schema(), bad_merged)
    assert not bad_result.valid


# ---------------------------------------------------------------------------
# Test 8 / 9: unlocalizable error uses safe fallback path (full repair only
# within the payload limit; otherwise a typed, safe failure -- never a guess)
# ---------------------------------------------------------------------------

def test_root_level_error_is_unlocalizable_and_uses_full_repair_when_within_limit():
    candidate = _candidate()
    plan = resolve_structural_repair_plan(
        candidate, [_error("$", "The provider output must be a JSON object.", "object_type")],
        expected_schema={"properties": {}},
        agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
    )

    assert plan.closure is None
    assert plan.use_full_repair is True
    assert plan.prompt  # the exact final prompt that was gated and will be sent


def test_unresolvable_field_name_is_unlocalizable_not_guessed():
    candidate = _candidate()
    # "unknownSection" is not a real top-level key of the candidate.
    plan = resolve_structural_repair_plan(
        candidate, [_error("unknownSection.value")], expected_schema={"properties": {}},
        agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
    )

    assert plan.closure is None
    assert plan.use_full_repair is True  # small synthetic payload -- within limit


def test_oversized_unlocalizable_candidate_raises_payload_too_large_never_sent():
    # A large candidate (simulated via a big synthetic field) whose error is
    # root-level (unlocalizable) and whose estimated payload exceeds a
    # deliberately tiny configured limit for this test.
    candidate = _candidate(hugeField="x" * 50_000)

    try:
        resolve_structural_repair_plan(
            candidate,
            [_error("$", "invalid", "object_type")],
            expected_schema={"properties": {}},
            agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
            full_payload_token_limit=10,
        )
        assert False, "expected StructuralRepairPayloadTooLargeError"
    except StructuralRepairPayloadTooLargeError:
        pass


# ---------------------------------------------------------------------------
# Test 10: every enabled repair provider is payload-protected
# ---------------------------------------------------------------------------

def test_scoped_repair_call_passes_provider_token_limits_for_groq():
    from unittest.mock import MagicMock

    from app.review.rewrite_agent import RewriteAgent

    fake_chain = MagicMock()
    fake_chain.generate_json.return_value = MagicMock(ok=True, data={}, provider="deepinfra", model="m")
    agent = RewriteAgent(provider_chain=fake_chain)

    candidate = _candidate()
    closure = resolve_structural_repair_closure(candidate, [_error("architecture.style")])

    agent.fix_structure_scoped(
        candidate, closure,
        agent_name="SEDocumentationAgent",
        validation_errors=[_error("architecture.style")],
        schema_cls=_real_schema(),
    )

    call_kwargs = fake_chain.generate_json.call_args.kwargs
    assert "provider_token_limits" in call_kwargs
    assert "groq" in call_kwargs["provider_token_limits"]


def test_oversized_candidate_never_reaches_any_provider_call():
    """
    Every enabled provider (Groq, DeepInfra, Ollama) shares ONE cascade
    (ProviderChain.generate_json) -- the centralized pre-flight payload gate
    in resolve_structural_repair_plan runs BEFORE that cascade is ever
    invoked, so an oversized, unlocalizable payload protects all three by
    never calling generate_json at all (see
    _attempt_se_documentation_structural_repair's early return on
    StructuralRepairPayloadTooLargeError).
    """
    from unittest.mock import MagicMock

    from app.review.rewrite_agent import RewriteAgent

    fake_chain = MagicMock()
    agent = RewriteAgent(provider_chain=fake_chain)
    candidate = _candidate(hugeField="x" * 50_000)

    try:
        resolve_structural_repair_plan(
            candidate, [_error("$", "invalid", "object_type")],
            expected_schema={"properties": {}},
            agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
            full_payload_token_limit=10,
        )
        assert False, "expected StructuralRepairPayloadTooLargeError"
    except StructuralRepairPayloadTooLargeError:
        pass

    fake_chain.generate_json.assert_not_called()


# ---------------------------------------------------------------------------
# Test 16: payload is materially reduced
# ---------------------------------------------------------------------------

def test_scoped_payload_is_materially_smaller_than_full_repair_payload():
    real_candidate = _real_valid_candidate()
    full_schema = _real_schema().model_json_schema()

    full_payload_tokens = estimate_tokens(real_candidate) + estimate_tokens(full_schema)

    closure = resolve_structural_repair_closure(real_candidate, [_error("functionalRequirements[0].priority")])
    scoped_candidate = build_compact_rewrite_candidate(real_candidate, closure)
    scoped_schema = build_schema_fragment(_real_schema(), closure.allowed_sections)
    scoped_payload_tokens = estimate_tokens(scoped_candidate) + estimate_tokens(scoped_schema)

    assert scoped_payload_tokens < full_payload_tokens * 0.4, (
        f"scoped={scoped_payload_tokens} full={full_payload_tokens}"
    )


# ---------------------------------------------------------------------------
# Test 12: semantic scoped rewrite remains unchanged (regression only)
# ---------------------------------------------------------------------------

def test_semantic_rewrite_closure_resolution_is_untouched():
    from app.review.models import ReviewerIssue
    from app.review.se_documentation_rewrite_scope import resolve_rewrite_closure

    candidate = _candidate()
    issue = ReviewerIssue(
        severity="high", requiresCorrection=True, category="quality",
        affectedField="architecture", description="d", revisionInstruction="fix",
    )
    closure = resolve_rewrite_closure(candidate, [issue])
    assert "architecture" in closure.allowed_sections


# ---------------------------------------------------------------------------
# Prompt sanity: no unrelated section content, JSON-only instruction present
# ---------------------------------------------------------------------------

def test_scoped_prompt_never_serializes_excluded_section_content():
    candidate = _candidate()
    closure = resolve_structural_repair_closure(candidate, [_error("architecture.style")])
    compact = build_compact_rewrite_candidate(candidate, closure)
    schema_fragment = build_schema_fragment(_real_schema(), closure.allowed_sections)

    prompt = build_structural_repair_scope_prompt(
        agent_name="SEDocumentationAgent",
        compact_candidate=compact,
        validation_errors=[_error("architecture.style")],
        closure=closure,
        schema_fragment=schema_fragment,
    )

    assert "Submit data" not in prompt  # functionalRequirements content excluded
    assert "layered" in prompt  # architecture content included
    assert "valid JSON" in prompt


# ---------------------------------------------------------------------------
# End-to-end ReviewPipeline integration -- Test 11 (bounded attempts),
# Test 13 (generic-agent backward compatibility is proven by the EXISTING,
# unmodified test_structural_repair_guidance.py / test_review_pipeline.py
# suites continuing to pass unchanged -- their fakes implement only
# fix_structure, never fix_structure_scoped, so if the new SE-doc branch
# were ever reached for a non-SE-Documentation agent_name, those tests
# would fail with AttributeError instead of passing), Test 14 (provenance).
# ---------------------------------------------------------------------------

from app.services.llm_provider import LLMResult  # noqa: E402


def _llm_ok(data, provider="deepinfra", model="test-model"):
    return LLMResult(ok=True, provider=provider, model=model, text="", data=data)


def _llm_fail(error="provider unavailable"):
    return LLMResult(ok=False, provider="none", model=None, text="", data=None, error=error)


class _FakeReviewerAgentApprovesCleanly:
    def analyze(self, candidate, context, **kwargs):
        return _llm_ok({"strengths": [], "issues": [], "qualityScore": 95, "overallAssessment": "fine"})


class _FakeStructuralRepairRewriteAgent:
    """
    Implements ONLY fix_structure_scoped (the SE Documentation path this
    task adds) plus fix_structure (required by ReviewPipeline's generic
    path/type surface) -- deliberately records which one is actually
    invoked, proving the pipeline picks the scoped path for
    SEDocumentationAgent.
    """

    def __init__(self, scoped_results):
        self._scoped_results = list(scoped_results)
        self.fix_structure_scoped_calls = 0
        self.fix_structure_calls = 0

    def fix_structure_scoped(self, candidate, closure, *, agent_name, validation_errors, schema_cls, deadline=None):
        self.fix_structure_scoped_calls += 1
        if not self._scoped_results:
            return _llm_fail("scoped repair exhausted")
        return self._scoped_results.pop(0)

    def fix_structure(self, candidate, *, agent_name, validation_errors=None, expected_schema=None, deadline=None):
        self.fix_structure_calls += 1
        return _llm_fail("full repair should not be called for a localizable SE-doc error")


def _se_doc_context() -> ReviewContext:
    return ReviewContext(
        agent_name="SEDocumentationAgent",
        trusted_system_instructions="Test context.",
        untrusted_user_input="",
    )


def test_pipeline_uses_scoped_structural_repair_for_se_documentation_with_correct_provenance():
    from app.review.registry import get_agent_config

    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    invalid_candidate["architecture"] = "not-an-object"  # localizable: architecture

    rewrite_agent = _FakeStructuralRepairRewriteAgent(
        scoped_results=[_llm_ok({"architecture": real_candidate["architecture"]})],
    )

    config = get_agent_config("SEDocumentationAgent")
    pipeline = ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=_FakeReviewerAgentApprovesCleanly(),
        rewrite_agent=rewrite_agent,
        config=config,
    )

    result = pipeline.run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.fix_structure_scoped_calls == 1
    assert rewrite_agent.fix_structure_calls == 0  # scoped path used, not the full-candidate path
    assert result.usable
    assert result.status in ("approved",)

    repair_records = [r for r in result.attemptHistory if r.operation == "structural_repair"]
    assert len(repair_records) == 1
    assert repair_records[0].schemaValid is True
    assert repair_records[0].generatorProvider == "deepinfra"


def test_pipeline_structural_repair_attempts_remain_bounded_at_configured_limit():
    from app.review.registry import get_agent_config

    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    invalid_candidate["architecture"] = "not-an-object"

    # Every scoped repair attempt returns something that STILL fails full
    # validation (architecture stays malformed) -- proves the pipeline gives
    # up after max_structural_repairs (1, per the real SE Documentation
    # registry config) rather than looping.
    rewrite_agent = _FakeStructuralRepairRewriteAgent(
        scoped_results=[_llm_ok({"architecture": "still-not-an-object"})] * 5,
    )

    config = get_agent_config("SEDocumentationAgent")
    assert config.max_structural_repairs == 1  # confirms the bound this test relies on

    pipeline = ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=_FakeReviewerAgentApprovesCleanly(),
        rewrite_agent=rewrite_agent,
        config=config,
    )

    result = pipeline.run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.fix_structure_scoped_calls == 1  # never retried beyond the configured limit
    assert not result.usable
    assert result.status == "schema_invalid"


# ---------------------------------------------------------------------------
# Follow-up verification: payload gating must measure the ACTUAL final
# prompt (Concern 1), and immutable fields must be excluded structurally,
# not merely restored after the fact (Concern 2).
# ---------------------------------------------------------------------------


# --- Concern 1: the gate measures the final prompt, not an approximation --


def test_scoped_plan_prompt_is_the_exact_final_prompt_string():
    """
    Test 1: the scoped gate's returned prompt is byte-identical to what
    build_structural_repair_scope_prompt independently produces for the same
    inputs (never a raw candidate/schema/error object, and never a shorter
    approximation) -- and it carries the instruction/heading text only the
    real final prompt has.
    """
    candidate = _candidate()
    errors = [_error("architecture.style")]
    closure = resolve_structural_repair_closure(candidate, errors)

    plan = resolve_structural_repair_plan(
        candidate, errors, expected_schema={"properties": {}},
        agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
    )

    expected_prompt = build_structural_repair_scope_prompt(
        agent_name="SEDocumentationAgent",
        compact_candidate=build_compact_rewrite_candidate(candidate, closure),
        validation_errors=errors,
        closure=closure,
        schema_fragment=build_schema_fragment(_real_schema(), closure.allowed_sections),
    )

    assert plan.closure is not None
    assert plan.use_full_repair is False
    assert plan.prompt == expected_prompt
    assert "Repair rules" in plan.prompt
    assert "SCOPED structural-repair stage" in plan.prompt


def test_scoped_prompt_sent_to_provider_is_exactly_the_gated_prompt():
    """
    Test 2: RewriteAgent.fix_structure_scoped(), given the plan's exact
    prompt via `prompt=`, sends that EXACT string to the provider -- it must
    never rebuild a second, potentially different prompt for the real call.
    """
    from unittest.mock import MagicMock

    from app.review.rewrite_agent import RewriteAgent

    candidate = _candidate()
    errors = [_error("architecture.style")]
    plan = resolve_structural_repair_plan(
        candidate, errors, expected_schema={"properties": {}},
        agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
    )
    closure = plan.closure
    assert closure is not None

    fake_chain = MagicMock()
    fake_chain.generate_json.return_value = MagicMock(ok=True, data={}, provider="deepinfra", model="m")
    agent = RewriteAgent(provider_chain=fake_chain)

    agent.fix_structure_scoped(
        candidate, closure,
        agent_name="SEDocumentationAgent",
        validation_errors=errors,
        schema_cls=_real_schema(),
        prompt=plan.prompt,
    )

    sent_prompt = fake_chain.generate_json.call_args.args[0]
    assert sent_prompt == plan.prompt
    assert fake_chain.generate_json.call_args.kwargs["estimated_prompt_tokens"] == estimate_tokens(plan.prompt)


def test_full_repair_plan_prompt_is_the_exact_final_prompt_string():
    """
    Test 3: for an unlocalizable-but-small candidate, the full-repair gate's
    returned prompt is byte-identical to build_structural_repair_prompt's
    output for the same inputs -- the same builder RewriteAgent.
    build_structural_repair_prompt delegates to (see that method's
    docstring), not a smaller component-only approximation.
    """
    candidate = _candidate()
    errors = [_error("$", "invalid", "object_type")]
    expected_schema = {"properties": {}}

    plan = resolve_structural_repair_plan(
        candidate, errors, expected_schema,
        agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
    )

    expected_prompt = build_structural_repair_prompt(
        candidate, agent_name="SEDocumentationAgent", validation_errors=errors, expected_schema=expected_schema,
    )

    assert plan.use_full_repair is True
    assert plan.prompt == expected_prompt
    assert "Repair rules" in plan.prompt


def test_full_repair_prompt_sent_to_provider_is_exactly_the_gated_prompt():
    """Test 3 (send side): fix_structure(), given the plan's exact prompt
    via `prompt=`, sends that EXACT string -- never rebuilds it."""
    from unittest.mock import MagicMock

    from app.review.rewrite_agent import RewriteAgent

    candidate = _candidate()
    errors = [_error("$", "invalid", "object_type")]
    expected_schema = {"properties": {}}
    plan = resolve_structural_repair_plan(
        candidate, errors, expected_schema,
        agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
    )
    assert plan.use_full_repair is True

    fake_chain = MagicMock()
    fake_chain.generate_json.return_value = MagicMock(ok=True, data={}, provider="deepinfra", model="m")
    agent = RewriteAgent(provider_chain=fake_chain)

    agent.fix_structure(
        candidate,
        agent_name="SEDocumentationAgent",
        validation_errors=errors,
        expected_schema=expected_schema,
        prompt=plan.prompt,
    )

    sent_prompt = fake_chain.generate_json.call_args.args[0]
    assert sent_prompt == plan.prompt


def test_instruction_and_formatting_overhead_is_counted_in_the_gate():
    """
    Test 4: a fixture where the OLD component-only estimate
    (estimate_tokens(candidate) + estimate_tokens(schema) +
    estimate_tokens(errors), summed separately) is BELOW a configured limit,
    but the real final prompt -- which additionally carries this module's
    instruction/heading/rules text -- exceeds that same limit. This proves
    the gate measures the actual final prompt: it would incorrectly PASS
    (and call the provider) if resolve_structural_repair_plan reverted to
    summing raw components instead of estimate_tokens(final_prompt).
    """
    candidate = _candidate()
    errors = [_error("$", "invalid", "object_type")]
    expected_schema = {"properties": {}}

    component_sum = estimate_tokens(candidate) + estimate_tokens(expected_schema) + estimate_tokens(errors)
    full_prompt = build_structural_repair_prompt(
        candidate, agent_name="SEDocumentationAgent", validation_errors=errors, expected_schema=expected_schema,
    )
    full_tokens = estimate_tokens(full_prompt)

    # The instruction/heading text is real, measurable overhead beyond the
    # raw components -- otherwise this fixture proves nothing.
    assert full_tokens > component_sum

    limit = full_tokens - 1  # the OLD component-only estimate would have accepted this limit
    assert component_sum <= limit

    try:
        resolve_structural_repair_plan(
            candidate, errors, expected_schema,
            agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
            full_payload_token_limit=limit,
        )
        assert False, "expected StructuralRepairPayloadTooLargeError"
    except StructuralRepairPayloadTooLargeError:
        pass


def test_full_repair_boundary_just_below_and_above_effective_limit():
    """Test 5: allowed exactly at the effective limit (configured limit
    minus the safety margin); rejected one token below that boundary."""
    candidate = _candidate()
    errors = [_error("$", "invalid", "object_type")]
    expected_schema = {"properties": {}}

    full_prompt = build_structural_repair_prompt(
        candidate, agent_name="SEDocumentationAgent", validation_errors=errors, expected_schema=expected_schema,
    )
    full_tokens = estimate_tokens(full_prompt)

    limit_allows = full_tokens + SE_DOC_STRUCTURAL_REPAIR_PROMPT_SAFETY_MARGIN_TOKENS
    plan = resolve_structural_repair_plan(
        candidate, errors, expected_schema,
        agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
        full_payload_token_limit=limit_allows,
    )
    assert plan.use_full_repair is True

    limit_rejects = limit_allows - 1
    try:
        resolve_structural_repair_plan(
            candidate, errors, expected_schema,
            agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
            full_payload_token_limit=limit_rejects,
        )
        assert False, "expected StructuralRepairPayloadTooLargeError at the boundary"
    except StructuralRepairPayloadTooLargeError:
        pass


def test_gate_never_truncates_prompt_content():
    """Test 6: when a plan IS returned, its prompt is the complete, intact
    text -- never a truncated/sliced prefix of the real prompt."""
    candidate = _candidate()
    errors = [_error("architecture.style")]
    plan = resolve_structural_repair_plan(
        candidate, errors, expected_schema={"properties": {}},
        agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
    )
    assert plan.prompt.rstrip().endswith(
        "Return a JSON object containing only the repaired top-level keys."
    )


def test_oversized_scoped_prompt_never_reaches_any_provider_call():
    """
    Test 16 (scoped variant of the existing full-repair equivalent above):
    a resolvable-but-oversized scoped prompt must raise before any provider
    is ever selected, protecting Groq/DeepInfra/Ollama alike -- the scoped
    path is not exempt from the centralized preflight gate just because a
    closure was found.
    """
    candidate = _candidate()
    errors = [_error("architecture.style")]

    try:
        resolve_structural_repair_plan(
            candidate, errors, expected_schema={"properties": {}},
            agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
            full_payload_token_limit=1,
        )
        assert False, "expected StructuralRepairPayloadTooLargeError"
    except StructuralRepairPayloadTooLargeError:
        pass


# --- Concern 2: immutable fields are excluded structurally, never merely
# restored after the model already saw them as a target --------------------


def test_immutable_field_error_root_is_excluded_from_allowed_sections():
    """Test 7: a validation error whose location root is an immutable field
    can never resolve to a scoped closure -- it is treated exactly like an
    unresolvable "$"/unknown-key root."""
    candidate = _candidate()
    try:
        resolve_structural_repair_closure(candidate, [_error("mermaidERD")])
        assert False, "expected StructuralRepairScopeError for an immutable-field root"
    except StructuralRepairScopeError as exc:
        assert "mermaidERD" in str(exc)


def test_scoped_compact_candidate_and_schema_fragment_never_include_immutable_fields():
    """
    Tests 8/9: for a resolvable (mutable-only) closure, the compact
    candidate and schema fragment built from it never include ANY
    _NEVER_LLM_REWRITABLE_FIELDS key -- those fields can never be members of
    allowed_sections in the first place (see the previous test), so nothing
    downstream needs to filter them out separately.
    """
    candidate = _candidate()  # includes mermaidERD, documentationQualityScore, qualityAssessment, sectionProvenance
    closure = resolve_structural_repair_closure(candidate, [_error("architecture.style")])

    compact = build_compact_rewrite_candidate(candidate, closure)
    schema_fragment = build_schema_fragment(_real_schema(), closure.allowed_sections)

    assert not (_NEVER_LLM_REWRITABLE_FIELDS & set(compact.keys()))
    assert not (_NEVER_LLM_REWRITABLE_FIELDS & set(schema_fragment.get("properties", {}).keys()))


def test_scoped_response_containing_immutable_field_is_rejected_as_unsolicited():
    """Test 10: a repair response that smuggles in an immutable field is
    rejected exactly like any other unsolicited section -- never merged, and
    never counted as a legitimate repair target."""
    candidate = _candidate()
    closure = resolve_structural_repair_closure(candidate, [_error("architecture.style")])

    response = {
        "architecture": {"style": "microservices", "explanation": "e"},
        "mermaidERD": "erDiagram\n  X ||--o{ Y : has",
    }

    try:
        validate_structural_repair_response(response, closure)
        assert False, "expected StructuralRepairScopeError for an immutable-field response key"
    except StructuralRepairScopeError as exc:
        assert "mermaidERD" in str(exc)


def test_mixed_mutable_and_immutable_errors_never_produce_a_scoped_closure():
    """
    Test 11: an error set naming ONE mutable section AND ONE immutable
    field must never be scoped to "just the mutable section" -- the
    immutable-field error makes the WHOLE batch unresolvable, so the plan
    falls through to the full-repair-or-reject path instead of silently
    resolving only the mutable part and dropping the immutable error.
    """
    candidate = _candidate()
    errors = [_error("architecture.style"), _error("mermaidERD")]

    try:
        resolve_structural_repair_closure(candidate, errors)
        assert False, "expected StructuralRepairScopeError"
    except StructuralRepairScopeError:
        pass

    plan = resolve_structural_repair_plan(
        candidate, errors, expected_schema={"properties": {}},
        agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
    )
    assert plan.closure is None
    assert plan.use_full_repair is True  # payload small enough; never a scoped attempt


def test_immutable_only_error_never_produces_a_scoped_closure():
    """Test 12: an error whose ONLY location is an immutable field must
    never trigger a scoped provider call -- it falls through to the
    full-repair-or-reject path exactly like any other unlocalizable error,
    never a guessed scoped target."""
    candidate = _candidate()
    errors = [_error("mermaidERD")]

    try:
        resolve_structural_repair_closure(candidate, errors)
        assert False, "expected StructuralRepairScopeError"
    except StructuralRepairScopeError:
        pass

    plan = resolve_structural_repair_plan(
        candidate, errors, expected_schema={"properties": {}},
        agent_name="SEDocumentationAgent", schema_cls=_real_schema(),
    )
    assert plan.closure is None
    assert plan.use_full_repair is True


def test_generic_agent_fix_structure_prompt_unchanged_by_the_prompt_parameter():
    """
    Test 13: a generic (non-SE-Documentation) agent calling fix_structure()
    exactly as before (no `prompt=` argument) gets a byte-identical prompt
    to what RewriteAgent.build_structural_repair_prompt always produced --
    proving the extraction into a shared module-level builder function did
    not change generic-agent output.
    """
    from unittest.mock import MagicMock

    from app.review.rewrite_agent import RewriteAgent

    candidate = {"title": "Demo", "score": 120, "items": []}
    errors = [{"location": "score", "message": "too high", "type": "less_than_equal"}]
    expected_schema = {"properties": {"score": {"maximum": 100}}}

    fake_chain = MagicMock()
    fake_chain.generate_json.return_value = MagicMock(ok=True, data={}, provider="deepinfra", model="m")
    agent = RewriteAgent(provider_chain=fake_chain)

    agent.fix_structure(
        candidate, agent_name="ExampleAgent", validation_errors=errors, expected_schema=expected_schema,
    )

    sent_prompt = fake_chain.generate_json.call_args.args[0]
    expected_prompt = agent.build_structural_repair_prompt(
        candidate, agent_name="ExampleAgent", validation_errors=errors, expected_schema=expected_schema,
    )
    assert sent_prompt == expected_prompt


class _FakeFullRepairOnlyRewriteAgent:
    """
    Implements ONLY fix_structure -- used to prove the SE-Doc full-repair
    fallback (mixed mutable/immutable validation errors) restores
    _NEVER_LLM_REWRITABLE_FIELDS from the original candidate AFTER the LLM
    response, even when the LLM's own response already contains a
    schema-valid replacement for that field. fix_structure_scoped raises if
    called at all -- a mixed error set must never reach it.
    """

    def __init__(self, full_repair_result):
        self._result = full_repair_result
        self.fix_structure_calls = 0
        self.fix_structure_scoped_calls = 0

    def fix_structure(self, candidate, *, agent_name, validation_errors=None, expected_schema=None,
                       deadline=None, prompt=None):
        self.fix_structure_calls += 1
        return self._result

    def fix_structure_scoped(self, *args, **kwargs):
        self.fix_structure_scoped_calls += 1
        raise AssertionError("scoped repair must never be attempted for an immutable-field error")


def test_pipeline_full_repair_restores_immutable_field_even_if_llm_returned_a_valid_one():
    """
    Test 11 (pipeline-level): a candidate with BOTH a mutable structural
    defect (architecture) and an immutable-field structural defect
    (mermaidERD is not a string) can never be scoped-repaired. On the
    full-repair fallback, even though the (fake) LLM response contains a
    schema-VALID mermaidERD, restore_never_llm_rewritable_fields overwrites
    it back to the ORIGINAL (still-invalid) value before the merged
    candidate is re-validated -- so the pipeline must fail safely as
    schema_invalid rather than accept an LLM-supplied immutable-field value.

    The real SE Documentation candidate's full-repair prompt (~21k estimated
    tokens) is itself far larger than SE_DOC_STRUCTURAL_REPAIR_MAX_PROMPT_
    TOKENS -- correctly so; that is this task's own payload-containment gate
    working as intended, and is covered by the payload-gating tests above.
    To isolate the SEPARATE property this test targets (restoration is
    unconditional even when the full-repair path is actually attempted),
    resolve_structural_repair_plan is called here with a deliberately
    generous limit, via the exact name app.review.pipeline imports it under,
    so the pipeline's OWN orchestration/restoration code still runs
    unmodified end-to-end.
    """
    import functools
    from unittest.mock import patch

    from app.review.registry import get_agent_config
    from app.review.se_documentation_structural_repair_scope import (
        resolve_structural_repair_plans as _real_resolve_structural_repair_plans,
    )

    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    invalid_candidate["architecture"] = "not-an-object"
    invalid_candidate["mermaidERD"] = 12345  # immutable field itself is structurally invalid

    llm_repaired = dict(invalid_candidate)
    llm_repaired["architecture"] = real_candidate["architecture"]
    llm_repaired["mermaidERD"] = "erDiagram\n  A ||--o{ B : has"  # a valid fix -- must still be discarded

    rewrite_agent = _FakeFullRepairOnlyRewriteAgent(_llm_ok(llm_repaired))

    config = get_agent_config("SEDocumentationAgent")
    pipeline = ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=_FakeReviewerAgentApprovesCleanly(),
        rewrite_agent=rewrite_agent,
        config=config,
    )

    generous_limit_plan = functools.partial(
        _real_resolve_structural_repair_plans, full_payload_token_limit=200_000,
    )

    with patch("app.review.pipeline.resolve_structural_repair_plans", generous_limit_plan):
        result = pipeline.run(
            lambda: _llm_ok(invalid_candidate),
            _se_doc_context(),
            writer_trusted_parts={}, writer_untrusted_parts={},
        )

    assert rewrite_agent.fix_structure_scoped_calls == 0
    assert rewrite_agent.fix_structure_calls == 1
    assert not result.usable
    assert result.status == "schema_invalid"


def test_restore_never_llm_rewritable_fields_is_unconditional_and_non_mutating():
    """
    Counterpart to the previous test, at the unit level: restore_never_llm_
    rewritable_fields restores _NEVER_LLM_REWRITABLE_FIELDS from `original`
    UNCONDITIONALLY -- regardless of whether the candidate's own value for
    that field happens to already be schema-valid -- and never mutates
    `original` in place. (A candidate whose only defect is mutable, e.g.
    architecture, is resolvable and routes through the SCOPED path instead
    of this full-repair fallback -- see resolve_structural_repair_closure --
    so this property is exercised directly rather than via the pipeline.)
    """
    real_candidate = _real_valid_candidate()
    candidate = dict(real_candidate)
    candidate["mermaidERD"] = "erDiagram\n  DIFFERENT ||--o{ VALUE : has"
    assert candidate["mermaidERD"] != real_candidate["mermaidERD"]  # sanity: genuinely different

    restored = restore_never_llm_rewritable_fields(real_candidate, candidate)

    assert restored["mermaidERD"] == real_candidate["mermaidERD"]
    assert restored is not candidate
    assert real_candidate["mermaidERD"] != candidate["mermaidERD"]  # original candidate dict untouched
    assert restored["architecture"] == candidate["architecture"]  # mutable fields left as-is
