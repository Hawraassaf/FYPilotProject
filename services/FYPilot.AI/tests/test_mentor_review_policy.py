"""
Regression tests proving the core review-policy invariant for
FypMentorAgent's REAL registry configuration (get_agent_config
("FypMentorAgent"), which has allow_unreviewed_output=True -- see
registry.py's FypMentorAgent entry for the full rationale):

    Reviewer unavailable  !=  Reviewer rejected

"Reviewer unavailable" (infrastructure failure: timeout, provider error,
malformed response) MAY surface the Writer's own candidate -- it already
passed both firewall passes and full schema validation, it was simply never
semantically reviewed. "Reviewer rejected" (the Reviewer successfully ran
and found a blocking defect) must NEVER be bypassed by allow_unreviewed_
output -- a rewrite failing, timing out, or exhausting its budget afterward
must never cause the REJECTED version to reappear as if it were safe.

Uses ReviewPipeline("FypMentorAgent", ...) with the REAL registry config
(not a synthetic AgentReviewConfig test double) so this test suite breaks
immediately if that config ever regresses. Reviewer/Rewrite stages use
lightweight fakes (no real LLM/network calls), matching test_review_pipeline
.py's existing _FakeReviewerAgent/_FakeRewriteAgent pattern.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.review.context import ReviewContext  # noqa: E402
from app.review.pipeline import ReviewPipeline  # noqa: E402
from app.review.registry import get_agent_config  # noqa: E402
from app.services.llm_provider import LLMResult  # noqa: E402


def _ok(data: dict) -> LLMResult:
    return LLMResult(ok=True, provider="deepinfra", model="m", text="", data=data)


def _fail(error: str) -> LLMResult:
    return LLMResult(ok=False, provider="none", model=None, text="", data=None, error=error)


def _mentor_answer(reply: str = "A valid mentoring answer.") -> dict:
    return {
        "reply": reply,
        "intent": "general_fyp_help",
        "usedContext": [],
        "suggestedNextActions": [],
        "warning": "",
        "confidence": 80,
        "assumptions": [],
        "codeBlocks": [],
    }


def _reviewer_ok(issues: list | None = None) -> LLMResult:
    return _ok({
        "strengths": [],
        "issues": issues or [],
        "qualityScore": 90,
        "overallAssessment": "assessment",
    })


def _issue(severity: str, category: str, requires_correction: bool = True) -> dict:
    return {
        "severity": severity,
        "requiresCorrection": requires_correction,
        "category": category,
        "affectedField": "reply",
        "description": "the reply contains a fabricated claim not supported by project context",
        "revisionInstruction": "remove or qualify the unsupported claim",
    }


class _FakeReviewerAgent:
    def __init__(self, results: list[LLMResult]):
        self._results = list(results)

    def analyze(self, candidate, context, **kwargs):
        if not self._results:
            raise AssertionError("Reviewer called more times than the test expected")
        return self._results.pop(0)


class _FakeRewriteAgent:
    def __init__(self, results: list[LLMResult] | None = None):
        self._results = list(results or [])

    def rewrite(self, candidate, blocking_issues, context, *, agent_name, deadline=None):
        if not self._results:
            return _fail("rewrite exhausted")
        return self._results.pop(0)

    def fix_structure(self, candidate, *, agent_name, validation_errors=None, expected_schema=None, deadline=None):
        return _fail("fix_structure should not be needed -- candidates are always schema-valid in these tests")


class _NeverCalledRewriteAgent:
    """Proves the rewrite stage is never invoked when the Reviewer approved
    (with or without warnings) -- matches TEST 6's requirement."""

    def rewrite(self, candidate, blocking_issues, context, *, agent_name, deadline=None):
        raise AssertionError("rewrite() must not be called when the Reviewer did not require one")

    def fix_structure(self, candidate, *, agent_name, validation_errors=None, expected_schema=None, deadline=None):
        raise AssertionError("fix_structure() must not be called in this scenario")


def _context() -> ReviewContext:
    return ReviewContext(
        agent_name="FypMentorAgent",
        trusted_system_instructions="FypMentorAgent: a specialized FYP mentor.",
        trusted_structural_context={"studentProfile.teamSize": 1},
        untrusted_project_text={"selectedIdea.title": "Test Project"},
        untrusted_user_input="What should I do next?",
        untrusted_conversation_history=[],
        untrusted_existing_code=None,
        untrusted_retrieved_web_content=[],
        previous_model_outputs=[],
        allowed_source_metadata=[],
    )


def _mentor_pipeline(*, reviewer_results, rewrite_results=None, rewrite_agent=None) -> ReviewPipeline:
    return ReviewPipeline(
        "FypMentorAgent",
        reviewer_agent=_FakeReviewerAgent(reviewer_results),
        rewrite_agent=rewrite_agent or _FakeRewriteAgent(rewrite_results),
        config=get_agent_config("FypMentorAgent"),
    )


class MentorReviewPolicyInvariantTests(unittest.TestCase):
    def setUp(self):
        # Regression guard: these tests only prove what they claim to prove
        # if FypMentorAgent's real registry config still has this flag set.
        self.assertTrue(get_agent_config("FypMentorAgent").allow_unreviewed_output)

    # ------------------------------------------------------------------
    # TEST 1 -- Reviewer infrastructure timeout
    # ------------------------------------------------------------------
    def test_reviewer_timeout_lets_the_valid_writer_answer_survive(self):
        pipeline = _mentor_pipeline(
            reviewer_results=[_fail("Reviewer request timed out")],
        )
        result = pipeline.run(
            lambda: _ok(_mentor_answer("Focus on Phase 2 of your roadmap next.")),
            _context(), writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertEqual(result.status, "review_unavailable")
        self.assertTrue(result.reviewUnavailable)
        self.assertTrue(result.usable, "A firewall-passed, schema-valid answer must survive a pure Reviewer timeout.")
        self.assertEqual(result.output["reply"], "Focus on Phase 2 of your roadmap next.")

    # ------------------------------------------------------------------
    # TEST 2 -- Reviewer provider unavailable (non-timeout infrastructure error)
    # ------------------------------------------------------------------
    def test_reviewer_provider_unavailable_lets_the_valid_writer_answer_survive(self):
        pipeline = _mentor_pipeline(
            reviewer_results=[_fail("All Reviewer providers failed: DeepInfra 503, Groq 500, Ollama connection refused")],
        )
        result = pipeline.run(
            lambda: _ok(_mentor_answer("Review your database schema before implementation.")),
            _context(), writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertEqual(result.status, "review_unavailable")
        self.assertTrue(result.usable)
        self.assertEqual(result.output["reply"], "Review your database schema before implementation.")

    # ------------------------------------------------------------------
    # TEST 3 -- Reviewer returns a blocking finding -> original must not
    # escape; a successful rewrite must be shown instead.
    # ------------------------------------------------------------------
    def test_blocking_finding_triggers_rewrite_and_original_is_never_shown(self):
        pipeline = _mentor_pipeline(
            reviewer_results=[
                _reviewer_ok(issues=[_issue("critical", "unsupported_claim")]),
                _reviewer_ok(issues=[]),  # re-review of the rewritten version: clean
            ],
            rewrite_results=[_ok(_mentor_answer("Corrected: no fabricated claim."))],
        )
        result = pipeline.run(
            lambda: _ok(_mentor_answer("Fabricated claim not grounded in project context.")),
            _context(), writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertIn(result.status, ("approved", "approved_with_minor_warnings"))
        self.assertTrue(result.usable)
        self.assertEqual(result.output["reply"], "Corrected: no fabricated claim.")
        self.assertNotEqual(result.output["reply"], "Fabricated claim not grounded in project context.")

    # ------------------------------------------------------------------
    # TEST 4 -- Blocking Reviewer finding + Rewrite hits the deadline
    # (modeled here as the rewrite call itself failing, the same path a
    # deadline-exhaustion takes -- see rewrite_agent.py's own deadline
    # handling, already covered separately by test_mentor_timeout_hardening.py).
    # ------------------------------------------------------------------
    def test_blocking_finding_with_rewrite_failure_never_returns_the_rejected_original(self):
        pipeline = _mentor_pipeline(
            reviewer_results=[_reviewer_ok(issues=[_issue("critical", "unsupported_claim")])],
            rewrite_results=[],  # rewrite_agent.rewrite() -> _fail("rewrite exhausted")
        )
        result = pipeline.run(
            lambda: _ok(_mentor_answer("Fabricated claim not grounded in project context.")),
            _context(), writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertNotEqual(
            result.output.get("reply"), "Fabricated claim not grounded in project context.",
            "The Reviewer-rejected original must never be returned just because the rewrite failed.",
        )
        self.assertFalse(
            result.usable and result.output.get("reply") == "Fabricated claim not grounded in project context.",
        )

    # ------------------------------------------------------------------
    # TEST 5 -- Blocking Reviewer finding + Rewrite provider failure
    # (explicit provider-error variant of TEST 4).
    # ------------------------------------------------------------------
    def test_blocking_finding_with_rewrite_provider_error_never_returns_the_rejected_original(self):
        class _ProviderErrorRewriteAgent:
            def rewrite(self, candidate, blocking_issues, context, *, agent_name, deadline=None):
                return _fail("RewriteAgent provider unavailable: all providers failed")

            def fix_structure(self, candidate, *, agent_name, validation_errors=None, expected_schema=None, deadline=None):
                return _fail("unused")

        pipeline = _mentor_pipeline(
            reviewer_results=[_reviewer_ok(issues=[_issue("high", "contradiction")])],
            rewrite_agent=_ProviderErrorRewriteAgent(),
        )
        result = pipeline.run(
            lambda: _ok(_mentor_answer("Contradicts the student's confirmed roadmap phase.")),
            _context(), writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertNotEqual(
            result.output.get("reply"), "Contradicts the student's confirmed roadmap phase.",
        )

    # ------------------------------------------------------------------
    # TEST 6 -- Minor (non-blocking) warning: Writer answer stays usable,
    # rewrite is never invoked.
    # ------------------------------------------------------------------
    def test_minor_warning_keeps_writer_answer_usable_without_rewrite(self):
        pipeline = _mentor_pipeline(
            reviewer_results=[_reviewer_ok(issues=[_issue("medium", "quality")])],
            rewrite_agent=_NeverCalledRewriteAgent(),
        )
        result = pipeline.run(
            lambda: _ok(_mentor_answer("A solid answer with one stylistic nitpick.")),
            _context(), writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertEqual(result.status, "approved_with_minor_warnings")
        self.assertTrue(result.usable)
        self.assertEqual(result.output["reply"], "A solid answer with one stylistic nitpick.")
        self.assertEqual(result.attempts, 1)

    # ------------------------------------------------------------------
    # TEST 7 -- Correctable issue + successful rewrite: bounded to exactly
    # one rewrite, then returns the corrected result.
    # ------------------------------------------------------------------
    def test_correctable_issue_triggers_exactly_one_rewrite_then_returns_corrected_result(self):
        pipeline = _mentor_pipeline(
            reviewer_results=[
                _reviewer_ok(issues=[_issue("high", "missing_mandatory_content")]),
                _reviewer_ok(issues=[]),
            ],
            rewrite_results=[_ok(_mentor_answer("Corrected with the missing mandatory content included."))],
        )
        result = pipeline.run(
            lambda: _ok(_mentor_answer("Missing required content.")),
            _context(), writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertTrue(result.usable)
        self.assertEqual(result.attempts, 2)  # writer attempt(0) + one rewrite attempt(1) -> attempt+1==2
        self.assertEqual(result.output["reply"], "Corrected with the missing mandatory content included.")

    def test_second_blocking_finding_after_the_one_allowed_rewrite_does_not_loop_forever(self):
        """Bounded rewrite count: max_rewrites=1 for FypMentorAgent -- a
        SECOND blocking finding (even non-critical) after the rewrite budget
        is exhausted must terminate, never attempt a second rewrite."""
        pipeline = _mentor_pipeline(
            reviewer_results=[
                _reviewer_ok(issues=[_issue("high", "contradiction")]),
                _reviewer_ok(issues=[_issue("high", "contradiction")]),
            ],
            rewrite_results=[_ok(_mentor_answer("Still contradicts something."))],
        )
        result = pipeline.run(
            lambda: _ok(_mentor_answer("Original contradiction.")),
            _context(), writer_trusted_parts={}, writer_untrusted_parts={},
        )

        # Exactly one rewrite was attempted (both reviewer_results consumed,
        # only one rewrite_results entry existed) -- reaching this line at
        # all (rather than an AssertionError from the reviewer running out
        # of queued results) proves the loop terminated instead of looping.
        #
        # A "high" (not "critical") severity blocking issue surviving the
        # rewrite budget ships as "unresolved"/usable=True rather than being
        # discarded -- rejecting an entire answer over one surviving
        # high-severity (not critical) issue was tried and reverted as a
        # product decision (see pipeline.py's exhausted-rewrite-budget
        # branch): showing the rewritten candidate with an honest warning
        # naming the real issue beats returning nothing. Only a genuinely
        # critical surviving issue still rejects outright.
        self.assertEqual(result.status, "unresolved")
        self.assertTrue(result.usable)
        self.assertEqual(result.output["reply"], "Still contradicts something.")
        self.assertIn("high", result.warning)
        self.assertIn("contradiction", result.warning)


if __name__ == "__main__":
    unittest.main()
