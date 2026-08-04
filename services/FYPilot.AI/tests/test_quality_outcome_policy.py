"""
Regression tests for Step 5 of the stabilization task: connecting the
semantic Reviewer's ACTUAL findings to the persisted quality score, via the
one dedicated deterministic function quality_outcome_policy.
apply_review_outcome_to_quality -- see that module's docstring for the full
policy.

These tests prove the logical invariants the task requires, not arbitrary
target numbers:
- a clean, fully-reviewed candidate can still score high (no cap applied
  when nothing was found);
- cross-domain contamination cannot leave projectSpecificity at 100;
- an architecture/AI-technical-report contradiction cannot leave
  crossSectionConsistency at 100;
- a semantically-false-but-referentially-valid traceability finding cannot
  leave traceabilityCoverage at 100 merely because the ids exist;
- an unresolved material finding caps the overall score and is recorded;
- reviewUnavailable=True caps the overall score to reflect limited
  confidence without touching usable/displayable (the pipeline's own job).

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.se_documentation.quality_outcome_policy import (  # noqa: E402
    apply_review_outcome_to_quality,
)
from app.agents.se_documentation.se_documentation_orchestrator import (  # noqa: E402
    QualityAssessmentDto,
)
from app.review.models import PipelineResult, ReviewerFindings, ReviewerIssue  # noqa: E402
from app.review.review_decision_engine import ReviewDecisionEngine  # noqa: E402


def _clean_assessment() -> QualityAssessmentDto:
    return QualityAssessmentDto(
        overallScore=95,
        criterionScores={
            "completeness": 100,
            "requirementTestability": 100,
            "crossSectionConsistency": 100,
            "traceabilityCoverage": 100,
            "projectSpecificity": 100,
            "diagramValidity": 100,
            "assumptionTransparency": 100,
            "databaseQuality": 100,
            "contentDepth": 80,
        },
    )


def _issue(category: str, field: str, severity: str = "high") -> ReviewerIssue:
    return ReviewerIssue(
        severity=severity,
        requiresCorrection=True,
        category=category,
        affectedField=field,
        description="a defect was found",
        revisionInstruction="fix it",
    )


def _result(*, status: str, usable: bool, issues=None, review_unavailable: bool = False) -> PipelineResult:
    findings = ReviewerFindings(issues=issues or [])
    decision = ReviewDecisionEngine().decide(findings, schema_ok=True)
    return PipelineResult(
        status=status,
        usable=usable,
        output={"marker": 1} if usable else {},
        reviewUnavailable=review_unavailable,
        reviewerFindings=findings,
        decision=decision,
    )


class QualityOutcomePolicyTests(unittest.TestCase):
    def test_clean_reviewed_candidate_keeps_a_high_score(self):
        base = _clean_assessment()
        result = _result(status="approved", usable=True, issues=[])

        final = apply_review_outcome_to_quality(base, result, {})

        self.assertEqual(final.overallScore, base.overallScore)
        self.assertEqual(final.criterionScores, base.criterionScores)

    def test_cross_domain_contamination_caps_project_specificity(self):
        base = _clean_assessment()
        result = _result(
            status="unresolved", usable=True,
            issues=[_issue("project_alignment", "functionalRequirements", severity="critical")],
        )

        final = apply_review_outcome_to_quality(base, result, {})

        self.assertLess(final.criterionScores["projectSpecificity"], 100)
        self.assertLess(final.overallScore, base.overallScore)

    def test_ai_report_contradiction_caps_cross_section_consistency(self):
        base = _clean_assessment()
        result = _result(
            status="unresolved", usable=True,
            issues=[_issue("contradiction", "aiTechnicalReport.taskType")],
        )

        final = apply_review_outcome_to_quality(base, result, {})

        self.assertLess(final.criterionScores["crossSectionConsistency"], 100)

    def test_semantic_traceability_failure_caps_traceability_even_with_valid_ids(self):
        base = _clean_assessment()
        result = _result(
            status="unresolved", usable=True,
            issues=[_issue("contradiction", "traceabilityMatrix[FR-01].moduleIds")],
        )

        final = apply_review_outcome_to_quality(base, result, {})

        self.assertLess(final.criterionScores["traceabilityCoverage"], 100)

    def test_wrong_role_authorization_issue_does_not_silently_keep_a_perfect_score(self):
        base = _clean_assessment()
        result = _result(
            status="unresolved", usable=True,
            issues=[_issue("contradiction", "uiScreens[UI-01].authorizedRoles")],
        )

        final = apply_review_outcome_to_quality(base, result, {})

        # An unresolved material (contradiction) finding always caps the
        # headline score, even when the affected field doesn't map to one
        # of the three specifically-named criteria above.
        self.assertLessEqual(final.overallScore, 75)

    def test_unresolved_status_caps_overall_score_and_records_why(self):
        base = _clean_assessment()
        result = _result(
            status="unresolved", usable=True,
            issues=[_issue("contradiction", "architecture")],
        )

        final = apply_review_outcome_to_quality(base, result, {})

        self.assertLessEqual(final.overallScore, 75)
        self.assertTrue(any("unresolved" in w.lower() for w in final.warnings))

    def test_review_unavailable_caps_overall_score_without_touching_usability(self):
        base = _clean_assessment()
        result = _result(status="review_unavailable", usable=True, issues=[], review_unavailable=True)

        final = apply_review_outcome_to_quality(base, result, {})

        self.assertLessEqual(final.overallScore, 70)
        self.assertTrue(any("structural checks only" in w.lower() for w in final.warnings))
        # This function must never touch usable/displayable -- that stays
        # entirely the pipeline's own responsibility (allow_unreviewed_output).
        self.assertTrue(result.usable)

    def test_overall_score_is_recomputed_from_capped_criteria_not_left_stale(self):
        base = _clean_assessment()
        result = _result(
            status="approved_with_minor_warnings", usable=True,
            issues=[_issue("project_alignment", "functionalRequirements", severity="low")],
        )

        final = apply_review_outcome_to_quality(base, result, {})

        # A "low"-severity, non-blocking project_alignment finding still
        # caps the specificity criterion (the policy applies to any
        # matching category, not only blocking ones) -- and overallScore
        # must reflect that cap rather than staying at the pre-cap value.
        self.assertLess(final.criterionScores["projectSpecificity"], 100)
        self.assertLess(final.overallScore, base.overallScore)


if __name__ == "__main__":
    unittest.main()
