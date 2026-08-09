"""
Tests for the per-section structural-repair queue (ReviewPipeline's
_attempt_se_documentation_structural_repair, together with
se_documentation_structural_repair_scope.resolve_structural_repair_plans).

Converts the old "bundle every affected section into one LLM repair
response" behavior into "one bounded LLM call per affected section" -- see
the module docstrings in se_documentation_structural_repair_scope.py and
pipeline.py's _MIN_SECONDS_PER_SECTION_REPAIR_ATTEMPT for the full rationale
(a bundled multi-section correction response can be large enough to hit a
provider's max_tokens ceiling, truncate, and force a fallback to an earlier,
structurally-valid-but-semantically-wrong document -- the live medical-triage
/ StockTransaction / Product contamination incident this task fixes).

Numbered comments map to the required-tests list from the fix specification.
"""

from __future__ import annotations

import time

from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.se_documentation_rewrite_scope import build_compact_rewrite_candidate
from app.review.se_documentation_structural_repair_scope import (
    resolve_structural_repair_plans,
)
from app.services import json_reliability as jr
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


def _llm_fail(error="provider unavailable", parse_diagnostics=None):
    return LLMResult(
        ok=False, provider="none", model=None, text="", data=None, error=error,
        parse_diagnostics=parse_diagnostics,
    )


def _llm_truncated(error="Provider output was truncated before valid JSON completed."):
    return _llm_fail(error=error, parse_diagnostics={
        "initialJsonValid": False, "isTruncated": True, "repairAttempted": False,
        "repairMethod": None, "repairSuccess": False, "repairedCharCount": None,
        "errorContext": {"line": 1, "column": 1, "position": 0, "context": ""},
    })


class _FakeReviewerAgentApprovesCleanly:
    def analyze(self, candidate, context, **kwargs):
        return _llm_ok({"strengths": [], "issues": [], "qualityScore": 95, "overallAssessment": "fine"})


class _FakeReviewerAgentFlagsCritical:
    def analyze(self, candidate, context, **kwargs):
        return _llm_ok({
            "strengths": [], "qualityScore": 10,
            "overallAssessment": "critical cross-project contamination detected",
            "issues": [{
                "severity": "critical",
                "affectedField": "databaseEntities",
                "description": "Contains unrelated inventory entities (StockTransaction, Product).",
                "revisionInstruction": "Remove unrelated entities.",
            }],
        })


class _FakeReviewerAgentProviderFails:
    def analyze(self, candidate, context, **kwargs):
        return _llm_fail("reviewer provider unavailable")


class _RecordingSectionRewriteAgent:
    """
    Records, per call, the section(s) requested (closure.allowed_sections),
    the deadline it was given, and any max_tokens_override -- then returns
    the next queued LLMResult for that section (or a failure if the queue is
    exhausted). fix_structure() is also implemented (returning a hard
    failure) so the pipeline's generic type surface is satisfied, and to
    prove the full-candidate path is never used for a localizable multi-
    section SE-Doc error.
    """

    def __init__(self, responses_by_section: dict[str, list[LLMResult]]):
        self._queues = {k: list(v) for k, v in responses_by_section.items()}
        self.call_log: list[str] = []  # section name per call, in call order
        self.deadlines_seen: list[float | None] = []
        self.max_tokens_overrides_seen: list[int | None] = []
        self.candidates_sent: list[dict] = []
        self.fix_structure_calls = 0

    def fix_structure_scoped(
        self, candidate, closure, *, agent_name, validation_errors, schema_cls,
        deadline=None, prompt=None, max_tokens_override=None,
    ):
        section = next(iter(closure.allowed_sections))
        self.call_log.append(section)
        self.deadlines_seen.append(deadline)
        self.max_tokens_overrides_seen.append(max_tokens_override)
        # Records what the REAL fix_structure_scoped would actually send as
        # the request payload (build_compact_rewrite_candidate(candidate,
        # closure)) -- not the full `candidate` this fake itself receives
        # (the real method internally narrows it; this fake's caller,
        # ReviewPipeline, always passes the complete merged-so-far document
        # plus the closure, exactly like the real fix_structure_scoped's own
        # signature).
        self.candidates_sent.append(build_compact_rewrite_candidate(candidate, closure))

        queue = self._queues.get(section, [])
        if not queue:
            return _llm_fail(f"no more queued responses for section {section!r}")
        return queue.pop(0)

    def fix_structure(self, candidate, *, agent_name, validation_errors=None, expected_schema=None, deadline=None, prompt=None):
        self.fix_structure_calls += 1
        return _llm_fail("full repair should not be called for a localizable multi-section SE-doc error")


def _se_doc_context() -> ReviewContext:
    return ReviewContext(
        agent_name="SEDocumentationAgent",
        trusted_system_instructions="Test context.",
        untrusted_user_input="",
    )


def _pipeline(rewrite_agent, reviewer_agent=None) -> ReviewPipeline:
    from app.review.registry import get_agent_config

    return ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=reviewer_agent or _FakeReviewerAgentApprovesCleanly(),
        rewrite_agent=rewrite_agent,
        config=get_agent_config("SEDocumentationAgent"),
    )


def _three_section_invalid_candidate() -> tuple[dict, dict]:
    """Real, otherwise-valid candidate with THREE independent sections made
    structurally invalid -- architecture, testingPlan, useCases (none is a
    dependent of another under _DEPENDENTS, and structural repair never
    widens via dependency closure anyway)."""
    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    invalid_candidate["architecture"] = "not-an-object"
    invalid_candidate["testingPlan"] = "not-a-list"
    invalid_candidate["useCases"] = "not-a-list"
    return real_candidate, invalid_candidate


# ---------------------------------------------------------------------------
# 1 & 2: multiple affected sections -> separate, deterministically ordered
# repair calls
# ---------------------------------------------------------------------------

def test_multiple_affected_sections_result_in_separate_deterministically_ordered_calls():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [_llm_ok({"testingPlan": real_candidate["testingPlan"]})],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.fix_structure_calls == 0
    assert rewrite_agent.call_log == ["architecture", "testingPlan", "useCases"]  # sorted, one call each
    assert result.usable
    assert result.status == "approved"

    # Every call sent exactly one section's content -- never a bundle.
    for candidate_sent in rewrite_agent.candidates_sent:
        assert len(candidate_sent) == 1


def test_repair_call_order_is_deterministic_regardless_of_validation_error_order():
    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    invalid_candidate["useCases"] = "not-a-list"
    invalid_candidate["architecture"] = "not-an-object"
    invalid_candidate["testingPlan"] = "not-a-list"

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [_llm_ok({"testingPlan": real_candidate["testingPlan"]})],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.call_log == sorted(rewrite_agent.call_log)


# ---------------------------------------------------------------------------
# 3: untouched sections are preserved
# ---------------------------------------------------------------------------

def test_untouched_sections_are_preserved_across_the_repair_queue():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [_llm_ok({"testingPlan": real_candidate["testingPlan"]})],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert result.output["projectTitle"] == real_candidate["projectTitle"]
    assert result.output["functionalRequirements"] == real_candidate["functionalRequirements"]
    assert result.output["mermaidERD"] == real_candidate["mermaidERD"]


# ---------------------------------------------------------------------------
# 4: each repair prompt contains only its target section
# ---------------------------------------------------------------------------

def test_each_repair_call_prompt_contains_only_its_target_section():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [_llm_ok({"testingPlan": real_candidate["testingPlan"]})],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    for section, candidate_sent in zip(rewrite_agent.call_log, rewrite_agent.candidates_sent):
        assert set(candidate_sent.keys()) == {section}


# ---------------------------------------------------------------------------
# 5: unsolicited section output is rejected
# ---------------------------------------------------------------------------

def test_unsolicited_section_in_a_queued_response_is_rejected_others_unaffected():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        # Response for testingPlan smuggles in an extra, unsolicited key.
        "testingPlan": [_llm_ok({
            "testingPlan": real_candidate["testingPlan"],
            "useCases": real_candidate["useCases"],
        })],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    # testingPlan's repair is rejected (still "not-a-list"), so the merged
    # candidate never becomes fully schema-valid overall.
    assert result.status == "schema_invalid"
    assert not result.usable


# ---------------------------------------------------------------------------
# 6: missing target section output is rejected
# ---------------------------------------------------------------------------

def test_missing_target_section_in_a_queued_response_is_rejected():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        # Empty object -- missing the one requested key entirely.
        "testingPlan": [_llm_ok({})],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert result.status == "schema_invalid"
    assert not result.usable


# ---------------------------------------------------------------------------
# 7 & 8 & 9: one truncated section retried at most once; max_tokens maps to
# output_truncated; retry uses the SAME absolute deadline
# ---------------------------------------------------------------------------

def test_output_truncated_classification_from_stop_reason_max_tokens():
    assert jr.classify_truncation_failure({"isTruncated": True}) == jr.OUTPUT_TRUNCATED
    assert jr.classify_truncation_failure({"isTruncated": False}) == jr.SCHEMA_VALIDATION_FAILURE
    assert jr.classify_truncation_failure(None) == jr.SCHEMA_VALIDATION_FAILURE


def test_truncated_section_is_retried_exactly_once_and_succeeds():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        # First attempt truncates; the retry succeeds.
        "testingPlan": [
            _llm_truncated(),
            _llm_ok({"testingPlan": real_candidate["testingPlan"]}),
        ],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.call_log.count("testingPlan") == 2  # original + exactly one retry
    assert result.usable
    assert result.status == "approved"

    # The retry asked for MORE tokens than the original (no override) call.
    testing_plan_indexes = [i for i, s in enumerate(rewrite_agent.call_log) if s == "testingPlan"]
    assert rewrite_agent.max_tokens_overrides_seen[testing_plan_indexes[0]] is None
    assert rewrite_agent.max_tokens_overrides_seen[testing_plan_indexes[1]] is not None


def test_truncated_section_retry_uses_the_same_absolute_deadline():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [
            _llm_truncated(),
            _llm_ok({"testingPlan": real_candidate["testingPlan"]}),
        ],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    # Every call across every section (including the retry) was given the
    # exact same absolute deadline value -- never a freshly recomputed one.
    non_none_deadlines = [d for d in rewrite_agent.deadlines_seen if d is not None]
    assert len(non_none_deadlines) == len(rewrite_agent.deadlines_seen)
    assert len(set(non_none_deadlines)) == 1


def test_truncated_section_is_never_retried_more_than_once():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    # BOTH the original attempt and the retry truncate -- must still only
    # ever see 2 calls for this section (1 original + 1 retry), never 3+.
    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [_llm_truncated(), _llm_truncated()],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.call_log.count("testingPlan") == 2
    assert result.status == "schema_invalid"  # testingPlan never got repaired


# ---------------------------------------------------------------------------
# 10: no call starts below the minimum remaining-time floor
# ---------------------------------------------------------------------------

def test_no_section_call_starts_below_the_minimum_remaining_time_floor():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [_llm_ok({"testingPlan": real_candidate["testingPlan"]})],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    from app.review.registry import get_agent_config

    pipeline = ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=_FakeReviewerAgentApprovesCleanly(),
        rewrite_agent=rewrite_agent,
        config=get_agent_config("SEDocumentationAgent"),
    )

    # Deadline is already in the past by the time the repair queue starts --
    # every section must be skipped (zero calls), never attempted with a
    # near-zero/negative remaining budget.
    past_deadline = time.monotonic() - 1.0

    result = pipeline.run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
        deadline=past_deadline,
    )

    assert rewrite_agent.call_log == []
    assert result.status in ("review_unavailable", "schema_invalid")
    assert not result.usable


# ---------------------------------------------------------------------------
# 11: repaired sections are validated before merge
# ---------------------------------------------------------------------------

def test_a_repaired_section_that_is_itself_still_schema_invalid_is_not_merged_as_success():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        # "Repaired" response for testingPlan is still the wrong shape.
        "testingPlan": [_llm_ok({"testingPlan": "still-not-a-list"})],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert result.status == "schema_invalid"
    assert not result.usable


# ---------------------------------------------------------------------------
# 12: failed section repair does not corrupt previously valid sections
# ---------------------------------------------------------------------------

def test_failed_section_repair_does_not_corrupt_previously_merged_sections():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    # architecture (first, alphabetically) succeeds; testingPlan then fails
    # outright (provider_failed) -- architecture's already-merged repair
    # must survive untouched in the final (still schema_invalid) output.
    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [_llm_fail("provider unavailable for this section")],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert result.status == "schema_invalid"
    # architecture and useCases were still attempted/repaired even though
    # testingPlan failed in between (deterministic sorted order).
    assert rewrite_agent.call_log == ["architecture", "testingPlan", "useCases"]


# ---------------------------------------------------------------------------
# 13: full-document validation and semantic review run after merging
# ---------------------------------------------------------------------------

def test_semantic_review_runs_on_the_merged_candidate_after_every_section_succeeds():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    reviewer_calls = []

    class _RecordingReviewer:
        def analyze(self, candidate, context, **kwargs):
            reviewer_calls.append(dict(candidate))
            return _llm_ok({"strengths": [], "issues": [], "qualityScore": 95, "overallAssessment": "fine"})

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [_llm_ok({"testingPlan": real_candidate["testingPlan"]})],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent, reviewer_agent=_RecordingReviewer()).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert len(reviewer_calls) == 1  # reviewed once, AFTER all three sections merged
    assert reviewer_calls[0]["architecture"] == real_candidate["architecture"]
    assert result.status == "approved"


# ---------------------------------------------------------------------------
# 14: critical cross-project contamination cannot become approved
# ---------------------------------------------------------------------------

def test_critical_semantic_contamination_after_repair_never_becomes_approved():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [_llm_ok({"testingPlan": real_candidate["testingPlan"]})],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent, reviewer_agent=_FakeReviewerAgentFlagsCritical()).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert result.status != "approved"
    assert result.status != "approved_with_minor_warnings"


# ---------------------------------------------------------------------------
# 15: structurally valid but unreviewed output is labeled as a draft
# ---------------------------------------------------------------------------

def test_structurally_repaired_but_unreviewable_output_is_labeled_not_approved():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
        "testingPlan": [_llm_ok({"testingPlan": real_candidate["testingPlan"]})],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]})],
    })

    result = _pipeline(rewrite_agent, reviewer_agent=_FakeReviewerAgentProviderFails()).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert result.status == "review_unavailable"
    assert result.status != "approved"
    assert result.reviewUnavailable is True
    if result.usable:
        assert "structural checks" in result.warning or "verified" in result.warning


# ---------------------------------------------------------------------------
# 16: provider/output provenance remains accurate
# ---------------------------------------------------------------------------

def test_provider_provenance_recorded_per_section_repair_attempt():
    real_candidate, invalid_candidate = _three_section_invalid_candidate()

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]}, provider="groq", model="groq-model")],
        "testingPlan": [_llm_ok({"testingPlan": real_candidate["testingPlan"]}, provider="deepinfra", model="di-model")],
        "useCases": [_llm_ok({"useCases": real_candidate["useCases"]}, provider="anthropic", model="claude")],
    })

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    repair_records = [r for r in result.attemptHistory if r.operation == "structural_repair"]
    assert len(repair_records) == 3
    providers = {r.generatorProvider for r in repair_records}
    assert providers == {"groq", "deepinfra", "anthropic"}


# ---------------------------------------------------------------------------
# 17: existing immutable-field protections remain intact
# ---------------------------------------------------------------------------

def test_immutable_only_error_resolves_to_a_single_full_repair_plan_never_a_section_queue():
    from app.review.registry import get_agent_config

    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    invalid_candidate["mermaidERD"] = 12345

    schema_cls = get_agent_config("SEDocumentationAgent").schema

    plans = resolve_structural_repair_plans(
        invalid_candidate,
        [{"location": "mermaidERD", "message": "must be a string", "type": "string_type"}],
        schema_cls.model_json_schema(),
        agent_name="SEDocumentationAgent",
        schema_cls=schema_cls,
        full_payload_token_limit=200_000,
    )

    assert len(plans) == 1
    assert plans[0].use_full_repair is True
    assert plans[0].closure is None


# ---------------------------------------------------------------------------
# 18: existing single-section repair behavior remains unchanged
# ---------------------------------------------------------------------------

def test_single_section_error_still_produces_exactly_one_repair_call():
    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    invalid_candidate["architecture"] = "not-an-object"

    rewrite_agent = _RecordingSectionRewriteAgent({
        "architecture": [_llm_ok({"architecture": real_candidate["architecture"]})],
    })

    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.call_log == ["architecture"]
    assert result.usable
    assert result.status == "approved"
