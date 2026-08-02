"""
ReviewDecisionEngine — deterministic. No LLM call.

Classifies semantic-review findings into:
- blocking issues: only verified material high/critical issues that explicitly
  require correction;
- warning issues: all other reviewer observations.

This keeps ordinary quality/style feedback from forcing a full rewrite while
still blocking serious unsupported, contradictory, missing, or misaligned
content.
"""

from app.review.models import ReviewerFindings, ReviewerIssue, RewriteDecision, Severity

_SEVERITY_RANK: dict[Severity, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

# Closed allowlist for material issues that may justify a semantic rewrite.
# "quality" and "consistency" are deliberately advisory here. A serious
# factual inconsistency should be classified by the Reviewer as
# "contradiction" or "project_alignment".
_MATERIAL_BLOCKING_CATEGORIES = {
    "unsupported_claim",
    "contradiction",
    "missing_mandatory_content",
    "project_alignment",
}


def _normalized_category(issue: ReviewerIssue) -> str:
    return issue.category.strip().lower()


def _is_blocking(issue: ReviewerIssue) -> bool:
    """
    Return True only for a material issue that all three checks support:

    1. The Reviewer explicitly requested correction.
    2. Severity is high or critical.
    3. The category belongs to the closed material allowlist.

    A low/medium issue never blocks. A high/critical style or general-quality
    observation remains a warning rather than forcing an expensive rewrite.
    """

    return (
        issue.requiresCorrection
        and issue.severity in ("high", "critical")
        and _normalized_category(issue) in _MATERIAL_BLOCKING_CATEGORIES
    )


class ReviewDecisionEngine:
    def decide(self, findings: ReviewerFindings, schema_ok: bool) -> RewriteDecision:
        blocking = [issue for issue in findings.issues if _is_blocking(issue)]
        warnings = [issue for issue in findings.issues if not _is_blocking(issue)]

        requires_rewrite = (not schema_ok) or bool(blocking)

        highest_blocking_severity: Severity | None = None
        if blocking:
            highest_blocking_severity = max(
                (issue.severity for issue in blocking),
                key=lambda severity: _SEVERITY_RANK[severity],
            )

        reason = self._build_reason(
            schema_ok=schema_ok,
            blocking=blocking,
            warnings=warnings,
        )

        return RewriteDecision(
            requiresRewrite=requires_rewrite,
            reason=reason,
            blockingIssues=blocking,
            warningIssues=warnings,
            highestBlockingSeverity=highest_blocking_severity,
        )

    def _build_reason(
        self,
        *,
        schema_ok: bool,
        blocking: list[ReviewerIssue],
        warnings: list[ReviewerIssue],
    ) -> str:
        if not schema_ok:
            return "Schema/hard-rule validation did not fully succeed."

        if not blocking:
            if warnings:
                return (
                    f"No blocking issues found; {len(warnings)} reviewer warning(s) "
                    "were preserved for display/audit."
                )
            return "No blocking issues found by the Reviewer."

        categories = sorted({_normalized_category(issue) for issue in blocking})
        warning_suffix = (
            f" {len(warnings)} additional non-blocking warning(s) were preserved."
            if warnings
            else ""
        )
        return (
            f"{len(blocking)} blocking issue(s) found in material categories: "
            f"{', '.join(categories)}.{warning_suffix}"
        )