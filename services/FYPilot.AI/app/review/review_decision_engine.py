"""
ReviewDecisionEngine — deterministic. No LLM call.

Decides whether a candidate output needs a rewrite, based only on the
*structured, already-extracted* fields the semantic Reviewer produced
(ReviewerFindings) plus whether the schema/hard-rule stage succeeded. This
keeps "why did it rewrite" answerable from code, not from a second black-box
model judgment, and it means qualityScore (a display/audit number) is never
consulted here — only the nature of the findings matters.
"""

from app.review.models import ReviewerFindings, ReviewerIssue, RewriteDecision, Severity

_SEVERITY_RANK: dict[Severity, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

# Categories that always count as blocking regardless of the Reviewer's
# requiresCorrection/severity choice for that issue — these represent
# fundamental problems (a missing mandatory section, a claim that contradicts
# the stored project context, an unsupported claim) rather than stylistic
# feedback.
_ALWAYS_BLOCKING_CATEGORIES = {
    "unsupported_claim",
    "contradiction",
    "missing_mandatory_content",
}


def _is_blocking(issue: ReviewerIssue) -> bool:
    return (
        issue.requiresCorrection
        or issue.severity in ("critical", "high")
        or issue.category in _ALWAYS_BLOCKING_CATEGORIES
    )


class ReviewDecisionEngine:
    def decide(self, findings: ReviewerFindings, schema_ok: bool) -> RewriteDecision:
        blocking = [issue for issue in findings.issues if _is_blocking(issue)]

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
        )

        return RewriteDecision(
            requiresRewrite=requires_rewrite,
            reason=reason,
            blockingIssues=blocking,
            highestBlockingSeverity=highest_blocking_severity,
        )

    def _build_reason(self, *, schema_ok: bool, blocking: list[ReviewerIssue]) -> str:
        if not schema_ok:
            return "Schema/hard-rule validation did not fully succeed."

        if not blocking:
            return "No blocking issues found by the Reviewer."

        categories = sorted({issue.category for issue in blocking})
        return f"{len(blocking)} blocking issue(s) found in categories: {', '.join(categories)}."
