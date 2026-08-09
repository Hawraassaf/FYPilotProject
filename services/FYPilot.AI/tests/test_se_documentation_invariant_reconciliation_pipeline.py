"""
Pipeline-level tests for SE Documentation's whole-document structural-
invariant reconciliation (ReviewPipeline._attempt_se_documentation_invariant_
reconciliation) -- the follow-up to the per-section repair queue that
handles an unlocalizable ("$") SEDocumentationCandidateSchema.
_check_structural_invariants failure without ever resending the complete
document for a full LLM repair.

Numbered comments map to the required-tests list from the fix specification.
"""

from __future__ import annotations

import time

from app.review.context import ReviewContext
from app.review.models import ReviewerIssue
from app.review.pipeline import ReviewPipeline, _PipelineState
from app.services.llm_provider import LLMResult


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


def _llm_ok(data, provider="deepinfra", model="test-model"):
    return LLMResult(ok=True, provider=provider, model=model, text="", data=data)


def _llm_fail(error="provider unavailable"):
    return LLMResult(ok=False, provider="none", model=None, text="", data=None, error=error)


def _se_doc_context() -> ReviewContext:
    return ReviewContext(
        agent_name="SEDocumentationAgent",
        trusted_system_instructions="Test context.",
        untrusted_user_input="",
    )


def _with_dangling_test_reference(candidate: dict) -> dict:
    """Makes the candidate's OWN whole-document invariant check fail with an
    unlocalizable ("$") error: a testingPlan entry references a functional
    requirement id that does not exist anywhere in the document. Everything
    else about the (large, real) candidate stays valid, so the ONLY
    violation diagnose_structural_invariants finds is this one dangling
    reference -- and the candidate is large enough (the real SE
    Documentation schema) that a full-document repair prompt would exceed
    the configured safe payload limit, exactly reproducing the live
    incident this reconciliation step exists for."""
    candidate = dict(candidate)
    testing_plan = [dict(item) for item in candidate.get("testingPlan") or []]
    if not testing_plan:
        testing_plan = [{"id": "TC-01", "title": "t", "type": "functional", "steps": [], "expectedResult": "e", "relatedRequirements": []}]
    testing_plan[0]["relatedRequirements"] = ["FR-99"]  # does not exist anywhere
    candidate["testingPlan"] = testing_plan
    return candidate


class _FakeReviewerAgentApprovesCleanly:
    def analyze(self, candidate, context, **kwargs):
        return _llm_ok({"strengths": [], "issues": [], "qualityScore": 95, "overallAssessment": "fine"})


class _FakeReviewerAgentFlagsCritical:
    def analyze(self, candidate, context, **kwargs):
        return _llm_ok({
            "strengths": [], "qualityScore": 10,
            "overallAssessment": "critical issue",
            "issues": [{
                "severity": "critical",
                "affectedField": "testingPlan",
                "description": "Unrelated critical content found.",
                "revisionInstruction": "Remove it.",
                "category": "contradiction",
                "requiresCorrection": True,
            }],
        })


class _RecordingRewriteAgent:
    """Implements rewrite_targeted (the invariant-reconciliation targeted
    correction call) and fix_structure/fix_structure_scoped (so the
    pipeline's earlier, unrelated structural-repair attempt -- which never
    fires in these tests since the FIRST validate_detailed call already
    goes straight to the unlocalizable-$-error path -- has a safe no-op
    implementation if ever reached)."""

    def __init__(self, rewrite_targeted_result: LLMResult | None = None):
        self._result = rewrite_targeted_result
        self.rewrite_targeted_calls = 0
        self.fix_structure_calls = 0
        self.fix_structure_scoped_calls = 0
        self.deadlines_seen: list[float | None] = []

    def rewrite_targeted(self, candidate, closure, blocking_issues, context, *, agent_name, schema_cls, deadline=None, max_tokens_override=None):
        self.rewrite_targeted_calls += 1
        self.deadlines_seen.append(deadline)
        return self._result if self._result is not None else _llm_fail("no result configured")

    def fix_structure(self, candidate, *, agent_name, validation_errors=None, expected_schema=None, deadline=None, prompt=None):
        self.fix_structure_calls += 1
        return _llm_fail("full repair must never be called for an unlocalizable invariant error")

    def fix_structure_scoped(self, candidate, closure, *, agent_name, validation_errors, schema_cls, deadline=None, prompt=None, max_tokens_override=None):
        self.fix_structure_scoped_calls += 1
        return _llm_fail("not used by these tests")


def _pipeline(rewrite_agent, reviewer_agent=None) -> ReviewPipeline:
    from app.review.registry import get_agent_config

    return ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=reviewer_agent or _FakeReviewerAgentApprovesCleanly(),
        rewrite_agent=rewrite_agent,
        config=get_agent_config("SEDocumentationAgent"),
    )


# ---------------------------------------------------------------------------
# 17: one targeted correction cycle succeeds
# ---------------------------------------------------------------------------

def test_one_targeted_correction_cycle_resolves_the_dangling_reference():
    real_candidate = _real_valid_candidate()
    invalid_candidate = _with_dangling_test_reference(real_candidate)

    existing_fr_id = real_candidate["functionalRequirements"][0]["id"] if real_candidate["functionalRequirements"] else "FR-01"
    fixed_testing_plan = [dict(item) for item in invalid_candidate["testingPlan"]]
    fixed_testing_plan[0]["relatedRequirements"] = [existing_fr_id]

    rewrite_agent = _RecordingRewriteAgent(
        rewrite_targeted_result=_llm_ok({"testingPlan": fixed_testing_plan}),
    )

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.rewrite_targeted_calls == 1
    assert rewrite_agent.fix_structure_calls == 0  # 19: full-document repair never used
    assert result.status == "approved"
    assert result.usable
    fixed_row = [r for r in result.output["testingPlan"] if r["id"] == fixed_testing_plan[0]["id"]][0]
    assert fixed_row["relatedRequirements"] == [existing_fr_id]


# ---------------------------------------------------------------------------
# 18: failed targeted correction returns unresolved (schema_invalid)
# ---------------------------------------------------------------------------

def test_failed_targeted_correction_returns_schema_invalid_not_approved():
    real_candidate = _real_valid_candidate()
    invalid_candidate = _with_dangling_test_reference(real_candidate)

    rewrite_agent = _RecordingRewriteAgent(rewrite_targeted_result=_llm_fail("provider unavailable"))

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.rewrite_targeted_calls == 1
    assert rewrite_agent.fix_structure_calls == 0  # 19: still never falls back to full-document repair
    assert result.status == "schema_invalid"
    assert result.status != "approved"


# ---------------------------------------------------------------------------
# 20: same absolute deadline preserved
# ---------------------------------------------------------------------------

def test_targeted_correction_uses_the_same_absolute_deadline():
    real_candidate = _real_valid_candidate()
    invalid_candidate = _with_dangling_test_reference(real_candidate)

    existing_fr_id = real_candidate["functionalRequirements"][0]["id"]
    fixed_testing_plan = [dict(item) for item in invalid_candidate["testingPlan"]]
    fixed_testing_plan[0]["relatedRequirements"] = [existing_fr_id]

    rewrite_agent = _RecordingRewriteAgent(rewrite_targeted_result=_llm_ok({"testingPlan": fixed_testing_plan}))

    from app.review.registry import get_agent_config

    pipeline = ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=_FakeReviewerAgentApprovesCleanly(),
        rewrite_agent=rewrite_agent,
        config=get_agent_config("SEDocumentationAgent"),
    )

    global_deadline = time.monotonic() + 1200.0
    pipeline.run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
        deadline=global_deadline,
    )

    assert rewrite_agent.deadlines_seen == [global_deadline]


# ---------------------------------------------------------------------------
# 21: structural success alone never implies semantic approval
# ---------------------------------------------------------------------------

def test_structural_reconciliation_success_does_not_bypass_semantic_review():
    real_candidate = _real_valid_candidate()
    invalid_candidate = _with_dangling_test_reference(real_candidate)

    existing_fr_id = real_candidate["functionalRequirements"][0]["id"]
    fixed_testing_plan = [dict(item) for item in invalid_candidate["testingPlan"]]
    fixed_testing_plan[0]["relatedRequirements"] = [existing_fr_id]

    rewrite_agent = _RecordingRewriteAgent(rewrite_targeted_result=_llm_ok({"testingPlan": fixed_testing_plan}))

    result = _pipeline(rewrite_agent, reviewer_agent=_FakeReviewerAgentFlagsCritical()).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    # The document became structurally valid (the targeted correction
    # succeeded), but a critical semantic finding still blocks approval.
    assert result.status != "approved"
    assert result.status != "approved_with_minor_warnings"


# ---------------------------------------------------------------------------
# 22: previously accepted documentation is preserved on final failure
# ---------------------------------------------------------------------------

def test_schema_invalid_result_preserves_previously_reviewed_candidate():
    from app.review.models import ReviewerFindings
    from app.review.registry import get_agent_config

    pipeline = ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=_FakeReviewerAgentApprovesCleanly(),
        rewrite_agent=_RecordingRewriteAgent(),
        config=get_agent_config("SEDocumentationAgent"),
    )

    preserved_output = {"projectTitle": "Previously reviewed, still safe document"}
    state = _PipelineState()
    state.last_reviewed_noncritical_candidate = (preserved_output, ReviewerFindings(issues=[]))

    result = pipeline._schema_invalid_result(state, "run-id", [], 1)

    assert result.status == "schema_invalid"
    assert result.usable
    assert result.output == preserved_output


# ---------------------------------------------------------------------------
# 23: provider/section provenance remains truthful
# ---------------------------------------------------------------------------

def test_deterministic_only_reconciliation_records_no_provider_attribution():
    """When reconciliation resolves everything WITHOUT any LLM call (the
    first-ever candidate has no baseline yet, so the duplicate/dangling
    fixture below is resolved purely deterministically), the recorded
    AttemptRecord must not attribute the fix to a provider/model that never
    ran."""
    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    frs = [dict(item) for item in invalid_candidate["functionalRequirements"]]
    if len(frs) < 1:
        frs = [{"id": "FR-01", "title": "A", "description": "d", "priority": "High", "source": "confirmed"}]
    duplicated = dict(frs[0])
    duplicated["title"] = duplicated["title"] + " (duplicate this pass)"
    frs.append(duplicated)
    invalid_candidate["functionalRequirements"] = frs

    rewrite_agent = _RecordingRewriteAgent()

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.rewrite_targeted_calls == 0  # resolved deterministically, no LLM call at all
    reconciliation_records = [r for r in result.attemptHistory if r.operation == "structural_repair"]
    assert reconciliation_records
    assert reconciliation_records[-1].generatorProvider is None
    assert reconciliation_records[-1].generatorModel is None


def test_plaintext_password_is_normalized_deterministically_with_zero_llm_calls():
    """
    A candidate whose ONLY structural-invariant problem is a plaintext
    Password field is now fixed by se_documentation_deterministic_
    normalization.py's unconditional normalization pre-pass (see this
    module's later coverage-expansion follow-up) -- WITHOUT ever calling
    rewrite_targeted/fix_structure/fix_structure_scoped. This supersedes the
    earlier visibility-only expectation for this specific kind.
    """
    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    entities = [dict(e) for e in invalid_candidate["databaseEntities"]]
    entities[0] = dict(entities[0])
    entities[0]["fields"] = [dict(f) for f in entities[0]["fields"]]
    entities[0]["fields"].append({
        "name": "Password", "dataType": "string", "nullable": False, "defaultValue": "",
        "description": "raw password", "constraints": "", "isSensitive": True,
        "isPrimaryKey": False, "isForeignKey": False, "referencedEntity": "", "referencedField": "",
    })
    invalid_candidate["databaseEntities"] = entities

    rewrite_agent = _RecordingRewriteAgent()

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.rewrite_targeted_calls == 0
    assert rewrite_agent.fix_structure_calls == 0
    assert rewrite_agent.fix_structure_scoped_calls == 0
    assert result.status == "approved"
    assert result.usable
    fields = [f["name"] for f in result.output["databaseEntities"][0]["fields"]]
    assert "Password" not in fields
    assert "PasswordHash" in fields


def test_targeted_correction_success_records_the_real_providers_attribution():
    real_candidate = _real_valid_candidate()
    invalid_candidate = _with_dangling_test_reference(real_candidate)

    existing_fr_id = real_candidate["functionalRequirements"][0]["id"]
    fixed_testing_plan = [dict(item) for item in invalid_candidate["testingPlan"]]
    fixed_testing_plan[0]["relatedRequirements"] = [existing_fr_id]

    rewrite_agent = _RecordingRewriteAgent(
        rewrite_targeted_result=_llm_ok({"testingPlan": fixed_testing_plan}, provider="anthropic", model="claude-sonnet-5"),
    )

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert result.status == "approved"
    reconciliation_records = [r for r in result.attemptHistory if r.operation == "structural_repair"]
    assert reconciliation_records[-1].generatorProvider == "anthropic"
    assert reconciliation_records[-1].generatorModel == "claude-sonnet-5"
