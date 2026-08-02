import pytest

from app.review.models import ReviewerFindings, ReviewerIssue
from app.review.review_decision_engine import ReviewDecisionEngine


def issue(
    *,
    severity: str,
    requires_correction: bool,
    category: str,
    field: str = "summary",
) -> ReviewerIssue:
    return ReviewerIssue(
        severity=severity,
        requiresCorrection=requires_correction,
        category=category,
        affectedField=field,
        description="Test issue",
        revisionInstruction="Fix the test issue",
    )


def decide(*issues: ReviewerIssue, schema_ok: bool = True):
    findings = ReviewerFindings(
        strengths=[],
        issues=list(issues),
        qualityScore=80,
        overallAssessment="Test",
    )
    return ReviewDecisionEngine().decide(findings, schema_ok=schema_ok)


def test_medium_quality_issue_is_warning_even_when_correction_requested():
    decision = decide(
        issue(
            severity="medium",
            requires_correction=True,
            category="quality",
        )
    )

    assert decision.requiresRewrite is False
    assert decision.blockingIssues == []
    assert len(decision.warningIssues) == 1


def test_high_quality_issue_is_warning_not_blocker():
    decision = decide(
        issue(
            severity="high",
            requires_correction=True,
            category="quality",
        )
    )

    assert decision.requiresRewrite is False
    assert len(decision.warningIssues) == 1


def test_high_unsupported_claim_is_blocking_when_correction_required():
    decision = decide(
        issue(
            severity="high",
            requires_correction=True,
            category="unsupported_claim",
        )
    )

    assert decision.requiresRewrite is True
    assert len(decision.blockingIssues) == 1
    assert decision.highestBlockingSeverity == "high"


def test_critical_contradiction_is_blocking_when_correction_required():
    decision = decide(
        issue(
            severity="critical",
            requires_correction=True,
            category="contradiction",
        )
    )

    assert decision.requiresRewrite is True
    assert decision.highestBlockingSeverity == "critical"


def test_high_material_issue_is_warning_when_reviewer_did_not_request_correction():
    decision = decide(
        issue(
            severity="high",
            requires_correction=False,
            category="contradiction",
        )
    )

    assert decision.requiresRewrite is False
    assert decision.blockingIssues == []
    assert len(decision.warningIssues) == 1


def test_project_alignment_is_a_material_blocking_category():
    decision = decide(
        issue(
            severity="high",
            requires_correction=True,
            category="project_alignment",
        )
    )

    assert decision.requiresRewrite is True
    assert len(decision.blockingIssues) == 1


def test_schema_failure_requires_rewrite_even_without_reviewer_issues():
    decision = decide(schema_ok=False)

    assert decision.requiresRewrite is True
    assert decision.reason == "Schema/hard-rule validation did not fully succeed."


def test_mixed_findings_are_separated_into_blockers_and_warnings():
    decision = decide(
        issue(
            severity="high",
            requires_correction=True,
            category="unsupported_claim",
            field="marketScore",
        ),
        issue(
            severity="medium",
            requires_correction=True,
            category="quality",
            field="summary",
        ),
        issue(
            severity="low",
            requires_correction=False,
            category="consistency",
            field="wording",
        ),
    )

    assert decision.requiresRewrite is True
    assert [item.affectedField for item in decision.blockingIssues] == ["marketScore"]
    assert [item.affectedField for item in decision.warningIssues] == ["summary", "wording"]