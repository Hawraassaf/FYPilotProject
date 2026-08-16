"""
End-to-end ReviewPipeline regression tests for MarketFootprintAgent's Gap 2
fix (allow_unreviewed_output False -> True), using the REAL ReviewPipeline/
registry config/MarketFootprintCandidateSchema, with fake reviewer/rewrite
agents standing in for the network calls.

Covers exactly the semantics table from the pre-freeze hardening pass:
1. Firewall-invalid Writer output -> still rejected.
2. Schema-invalid Writer output -> still rejected.
3. Writer valid + Reviewer succeeds -> normal ReviewDecisionEngine verdict.
4. Writer valid + Reviewer requests a legitimate rewrite -> existing bounded
   rewrite path (max_rewrites=1) still works, and the count is unchanged.
5. Writer valid + Reviewer fails/times out/returns unusable JSON -> the
   valid Writer candidate is preserved (status="review_unavailable",
   usable=True, outputReviewLevel="structural_only" -- never claiming the
   Reviewer approved something it never reviewed).

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.review.context import ReviewContext  # noqa: E402
from app.review.pipeline import ReviewPipeline  # noqa: E402
from app.review.registry import get_agent_config  # noqa: E402
from app.services.llm_provider import LLMResult  # noqa: E402


def _valid_candidate() -> dict:
    """A fully valid MarketFootprintCandidateSchema-conforming dict --
    exactly the 3 required regions, and the one region referencing a
    sourceUrl that actually exists in the top-level sources list."""
    source_url = "https://worldbank.org/mena-report"

    def region(key: str, name: str, source_urls: list[str]) -> dict:
        return {
            "regionKey": key,
            "regionName": name,
            "opportunityScore": 70,
            "confidenceScore": 60,
            "demandLevel": "Moderate",
            "competitionPressure": "medium",
            "evidenceSummary": "Solid evidence exists.",
            "scoreBreakdown": {
                "problemUrgency": 70, "geographicFit": 70, "adoptionReadiness": 70,
                "competitionGap": 70, "targetUserReachability": 70,
                "technologyMomentum": 70, "evidenceStrength": 60,
            },
            "sourceTitles": ["World Bank Regional Report"] if source_urls else [],
            "sourceUrls": source_urls,
        }

    return {
        "status": "ready",
        "provider": "groq",
        "modelUsed": "llama-3.3-70b-versatile",
        "groundedInLiveData": True,
        "overallOpportunityScore": 70,
        "overallConfidenceScore": 60,
        "overallDemandLevel": "Moderate",
        "bestLaunchMarket": "Lebanon",
        "bestLaunchReason": "Strongest local evidence.",
        "expansionPath": ["Lebanon", "MENA", "Global"],
        "whyDemanded": ["Real local demand.", "Growing interest."],
        "strategicRecommendation": "Start in Lebanon, then expand regionally.",
        "limitations": ["Evidence is limited for MENA."],
        "regions": [
            region("lebanon", "Lebanon", [source_url]),
            region("mena", "MENA", []),
            region("global", "Global", []),
        ],
        "sources": [{
            "title": "World Bank Regional Report",
            "url": source_url,
            "publisher": "worldbank.org",
            "relevance": "Regional demand evidence.",
            "relevanceScore": 80,
            "isVerified": True,
            "regions": ["Lebanon"],
        }],
        "analyzedAt": datetime.now(timezone.utc).isoformat(),
    }


def _writer_result() -> LLMResult:
    return LLMResult(
        ok=True, provider="groq", model="llama-3.3-70b-versatile",
        text="", data=_valid_candidate(),
    )


def _context() -> ReviewContext:
    return ReviewContext(
        agent_name="MarketFootprintAgent",
        trusted_system_instructions="",
        trusted_structural_context={"useSearch": True},
        untrusted_project_text={
            "projectTitle": "Study Room Booking Platform",
            "problemStatement": "Students struggle to find available study rooms.",
        },
        untrusted_user_input="",
        untrusted_conversation_history=[],
        untrusted_existing_code=None,
        untrusted_retrieved_web_content=[],
        previous_model_outputs=[],
        allowed_source_metadata=[
            {"url": "https://worldbank.org/mena-report"},
        ],
    )


class _FakeReviewerAlwaysFails:
    """Simulates provider outage / timeout / malformed JSON -- any of these
    surface identically to guarded_call as `provider_failed=True` on the
    reviewer stage (LLMResult.ok=False)."""

    def analyze(self, candidate, context, *, known_risky_claims, mandatory_fields,
                extra_rubric="", deadline=None, reporter=None):
        return LLMResult(ok=False, provider="groq", model="m", text="", data=None, error="simulated outage")


class _FakeReviewerMalformedJson:
    """Simulates a Reviewer call that technically succeeds at the transport
    level but returns JSON that doesn't validate against ReviewerFindings
    (wrong types for qualityScore/issues -- every ReviewerFindings field has
    a default, so a merely-unrecognized-keys payload would validate fine;
    this must actually fail type validation to be genuinely malformed)."""

    def analyze(self, candidate, context, *, known_risky_claims, mandatory_fields,
                extra_rubric="", deadline=None, reporter=None):
        return LLMResult(
            ok=True, provider="groq", model="m", text="",
            data={"issues": "not-a-list", "qualityScore": "not-a-number"},
        )


class _FakeReviewerApproves:
    def analyze(self, candidate, context, *, known_risky_claims, mandatory_fields,
                extra_rubric="", deadline=None, reporter=None):
        return LLMResult(
            ok=True, provider="groq", model="m", text="",
            data={"strengths": ["Grounded in real evidence."], "issues": [],
                  "qualityScore": 90, "overallAssessment": "Solid analysis."},
        )


class _FakeReviewerRequestsRewrite:
    """First call: flags a high-severity blocking issue. Any subsequent
    call (after the rewrite) approves -- proves the bounded rewrite path
    still works end to end."""

    def __init__(self):
        self.calls = 0

    def analyze(self, candidate, context, *, known_risky_claims, mandatory_fields,
                extra_rubric="", deadline=None, reporter=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResult(
                ok=True, provider="groq", model="m", text="",
                data={
                    "strengths": [],
                    "issues": [{
                        "severity": "high", "requiresCorrection": True,
                        "category": "unsupported_claim", "affectedField": "strategicRecommendation",
                        "description": "Overstates confidence.",
                        "revisionInstruction": "Soften the claim.",
                    }],
                    "qualityScore": 50, "overallAssessment": "Needs one correction.",
                },
            )
        return LLMResult(
            ok=True, provider="groq", model="m", text="",
            data={"strengths": ["Corrected."], "issues": [], "qualityScore": 85, "overallAssessment": "Fixed."},
        )


class _FakeRewriteAgent:
    def rewrite(self, candidate, blocking_issues, context, *, agent_name, deadline=None):
        fixed = dict(candidate)
        fixed["strategicRecommendation"] = "Start in Lebanon (evidence-qualified claim)."
        return LLMResult(ok=True, provider="groq", model="m", text="", data=fixed)


class ReviewerUnavailablePreservesValidCandidateTests(unittest.TestCase):
    """Cases 15-17: Reviewer outage/timeout/malformed JSON never discards an
    already firewall-safe, schema-valid Writer candidate."""

    def _run(self, reviewer_agent) -> "PipelineResult":  # noqa: F821
        pipeline = ReviewPipeline(
            "MarketFootprintAgent",
            reviewer_agent=reviewer_agent,
        )
        return pipeline.run(
            lambda: _writer_result(),
            _context(),
            writer_trusted_parts={},
            writer_untrusted_parts={},
        )

    def test_reviewer_provider_failure_preserves_candidate(self):
        result = self._run(_FakeReviewerAlwaysFails())

        self.assertEqual(result.status, "review_unavailable")
        self.assertTrue(result.usable)
        self.assertEqual(result.outputReviewLevel, "structural_only")
        self.assertIn("passed structural checks", result.warning)
        self.assertEqual(result.output["bestLaunchMarket"], "Lebanon")

    def test_reviewer_malformed_json_preserves_candidate(self):
        result = self._run(_FakeReviewerMalformedJson())

        self.assertEqual(result.status, "review_unavailable")
        self.assertTrue(result.usable)
        self.assertEqual(result.outputReviewLevel, "structural_only")

    def test_review_never_falsely_claims_approval(self):
        """The response must never imply the Reviewer approved this --
        status/outputReviewLevel must be the honest unavailable labels,
        never "approved"."""
        result = self._run(_FakeReviewerAlwaysFails())

        self.assertNotEqual(result.status, "approved")
        self.assertNotEqual(result.outputReviewLevel, "approved")
        self.assertTrue(result.reviewUnavailable)


class FirewallAndSchemaStillRejectTests(unittest.TestCase):
    """Cases 18-19: allow_unreviewed_output=True must never rescue a
    candidate that failed firewall or schema validation -- those gates run
    BEFORE state.last_structurally_valid_candidate is ever set."""

    def test_firewall_invalid_writer_output_still_rejected(self):
        malicious_candidate = dict(_valid_candidate())
        malicious_candidate["strategicRecommendation"] = (
            "See https://not-a-verified-source.example.com for details."
        )
        writer_result = LLMResult(
            ok=True, provider="groq", model="m", text="", data=malicious_candidate,
        )

        pipeline = ReviewPipeline("MarketFootprintAgent", reviewer_agent=_FakeReviewerApproves())
        result = pipeline.run(
            lambda: writer_result, _context(),
            writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertEqual(result.status, "firewall_blocked")
        self.assertFalse(result.usable)

    def test_schema_invalid_writer_output_still_rejected(self):
        broken_candidate = dict(_valid_candidate())
        broken_candidate["regions"] = broken_candidate["regions"][:2]  # only 2 of 3 regions

        # No structural-repair rewrite agent supplied -- the repair attempt
        # itself will fail/return nothing usable, proving this path still
        # ends in a non-usable schema_invalid/review_unavailable result
        # rather than silently accepting a structurally broken candidate.
        class _FakeRewriteAgentAlwaysFails:
            def fix_structure(self, candidate, *, agent_name, validation_errors,
                               expected_schema, deadline=None, prompt=None):
                return LLMResult(ok=False, provider="groq", model="m", text="", data=None, error="repair failed")

        writer_result = LLMResult(ok=True, provider="groq", model="m", text="", data=broken_candidate)

        pipeline = ReviewPipeline(
            "MarketFootprintAgent",
            reviewer_agent=_FakeReviewerApproves(),
            rewrite_agent=_FakeRewriteAgentAlwaysFails(),
        )
        result = pipeline.run(
            lambda: writer_result, _context(),
            writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertFalse(result.usable)
        self.assertIn(result.status, ("schema_invalid", "review_unavailable"))


class NormalReviewFlowUnchangedTests(unittest.TestCase):
    """Cases 20-22: the happy path, the rewrite path, and the rewrite cap
    are all completely unaffected by this policy flag."""

    def test_reviewer_success_normal_decision(self):
        pipeline = ReviewPipeline("MarketFootprintAgent", reviewer_agent=_FakeReviewerApproves())
        result = pipeline.run(
            lambda: _writer_result(), _context(),
            writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertIn(result.status, ("approved", "approved_with_minor_warnings"))
        self.assertTrue(result.usable)
        self.assertEqual(result.outputReviewLevel, "approved")

    def test_reviewer_requests_rewrite_bounded_path_still_works(self):
        reviewer = _FakeReviewerRequestsRewrite()
        pipeline = ReviewPipeline(
            "MarketFootprintAgent", reviewer_agent=reviewer, rewrite_agent=_FakeRewriteAgent(),
        )
        result = pipeline.run(
            lambda: _writer_result(), _context(),
            writer_trusted_parts={}, writer_untrusted_parts={},
        )

        self.assertTrue(result.usable)
        self.assertEqual(
            result.output["strategicRecommendation"],
            "Start in Lebanon (evidence-qualified claim).",
        )
        # Exactly one rewrite happened (reviewer.calls == 2: initial + post-rewrite re-review).
        self.assertEqual(reviewer.calls, 2)

    def test_max_rewrites_unchanged_at_one(self):
        self.assertEqual(get_agent_config("MarketFootprintAgent").max_rewrites, 1)
        self.assertEqual(get_agent_config("MarketFootprintAgent").max_semantic_rewrites, 1)
        self.assertEqual(get_agent_config("MarketFootprintAgent").max_structural_repairs, 1)


if __name__ == "__main__":
    unittest.main()
