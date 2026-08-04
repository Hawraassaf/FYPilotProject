"""
The single deterministic location where a completed semantic review outcome
is allowed to adjust SE Documentation's quality assessment.

Context: _compute_quality_assessment (se_documentation_orchestrator.py)
computes a BASE score from the assembled candidate at Writer time --
before ReviewPipeline.run() ever semantically reviews it. That base score
already penalizes deterministically-detectable problems (empty sections,
missing primary keys, plaintext password fields, ...), but has no way to
know what the semantic Reviewer itself found (cross-domain contamination,
wrong-role UI authorization, semantically-false-but-referentially-valid
traceability, an architecture/AI-technical-report contradiction) -- those
are judgment calls only the Reviewer's own findings capture.

apply_review_outcome_to_quality closes that gap WITHOUT letting the LLM
Reviewer assign any number itself: it reads only the deterministic,
normalized ReviewerIssue fields (category, affectedField, severity) already
produced by ReviewDecisionEngine, and applies a small, fixed set of caps --
the same caps/conventions _compute_quality_assessment itself already uses
elsewhere (60 for a detected cross-section inconsistency, 70 for limited
confidence, 75 for an unresolved finding) so no new arbitrary number is
invented here.

Call this exactly once, from the SE Documentation router, immediately after
ReviewPipeline.run() returns and before the result is handed back to .NET --
see routers/se_documentation.py. Do not duplicate this logic anywhere else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from app.agents.se_documentation.se_documentation_orchestrator import (
    QUALITY_CRITERION_WEIGHTS,
    QualityAssessmentDto,
)

if TYPE_CHECKING:
    from app.review.models import PipelineResult

# Reuses the exact cap values _compute_quality_assessment already applies
# for analogous situations (see that function's ai_conflict/auth_conflict,
# used_fallback, and untraced_count>=4 branches) -- this module invents no
# new numeric convention.
_CROSS_SECTION_INCONSISTENCY_CAP = 60
_UNRESOLVED_FINDING_CAP = 75
_LIMITED_CONFIDENCE_CAP = 70


def _recompute_overall(criterion_scores: Dict[str, int]) -> int:
    return round(sum(
        criterion_scores.get(name, 100) * weight
        for name, weight in QUALITY_CRITERION_WEIGHTS.items()
    ))


def apply_review_outcome_to_quality(
    base_assessment: QualityAssessmentDto,
    final_review_decision: "PipelineResult",
    section_provenance: Dict[str, str],
) -> QualityAssessmentDto:
    """
    Derive the FINAL, persisted quality assessment from the base
    (pre-review) assessment plus the pipeline's actual review outcome.

    Minimum policy (each backed by a regression test in
    tests/test_quality_outcome_policy.py):
    - A reviewer finding categorized "project_alignment" (cross-domain
      contamination, off-topic content) caps projectSpecificity.
    - A "contradiction" finding whose affectedField names the architecture
      or AI technical report caps crossSectionConsistency.
    - A "contradiction" finding whose affectedField names the traceability
      matrix caps traceabilityCoverage (an id existing is not sufficient for
      genuine coverage).
    - An unresolved material finding (status == "unresolved", or a
      surviving high/critical blocking issue) caps the overall score and
      records why.
    - reviewUnavailable=True (structural-only output) caps the overall
      score to reflect limited confidence -- this never changes usable/
      displayable, which the pipeline itself already governs.
    - A clean candidate with no matching findings is returned unchanged.

    section_provenance is accepted (not currently used for a numeric
    adjustment) so a future fallback-aware policy has a single place to add
    it -- see the stabilization task's Step 6, which keeps the current
    all-or-nothing fallback rejection policy and defers tiering.
    """
    criterion_scores = dict(base_assessment.criterionScores)
    warnings = list(base_assessment.warnings)
    overall = base_assessment.overallScore

    findings = final_review_decision.reviewerFindings
    all_issues = list(findings.issues) if findings else []

    def _cap_criterion(name: str, cap: int, note: str) -> None:
        if criterion_scores.get(name, 100) > cap:
            criterion_scores[name] = cap
            if note not in warnings:
                warnings.append(note)

    for issue in all_issues:
        category = issue.category.strip().lower()
        field = (issue.affectedField or "").lower()

        if category == "project_alignment":
            _cap_criterion(
                "projectSpecificity", _CROSS_SECTION_INCONSISTENCY_CAP,
                "A reviewer finding indicated content from an unrelated project "
                "domain; project specificity was capped accordingly.",
            )

        if category == "contradiction" and (
            "architecture" in field or "aitechnicalreport" in field or "ai technical report" in field
        ):
            _cap_criterion(
                "crossSectionConsistency", _CROSS_SECTION_INCONSISTENCY_CAP,
                "A reviewer finding indicated the architecture and AI technical "
                "report sections describe inconsistent AI approaches; cross-section "
                "consistency was capped accordingly.",
            )

        if category == "contradiction" and "traceab" in field:
            _cap_criterion(
                "traceabilityCoverage", _CROSS_SECTION_INCONSISTENCY_CAP,
                "A reviewer finding indicated a traceability row links to a real but "
                "unrelated id; traceability quality was capped accordingly (an id "
                "existing is not sufficient for genuine coverage).",
            )

    if criterion_scores != base_assessment.criterionScores:
        overall = _recompute_overall(criterion_scores)

    decision = final_review_decision.decision
    blocking = decision.blockingIssues if decision else []
    highest_blocking_severity = decision.highestBlockingSeverity if decision else None

    if final_review_decision.status == "unresolved" or (
        blocking and highest_blocking_severity in ("high", "critical")
    ):
        overall = min(overall, _UNRESOLVED_FINDING_CAP)
        note = (
            "One or more reviewer findings remained unresolved for this document; "
            "this score reflects that limitation."
        )
        if note not in warnings:
            warnings.append(note)

    if final_review_decision.reviewUnavailable:
        overall = min(overall, _LIMITED_CONFIDENCE_CAP)
        note = (
            "Semantic review could not be completed for this document; this score "
            "reflects structural checks only, not a full semantic review."
        )
        if note not in warnings:
            warnings.append(note)

    return base_assessment.model_copy(update={
        "overallScore": max(0, min(100, overall)),
        "criterionScores": criterion_scores,
        "warnings": warnings,
    })
