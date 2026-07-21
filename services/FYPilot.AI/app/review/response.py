"""
Shared "review" / AI Quality Passport response builder.

Extracted from app/routers/fyp_chat.py (the Mentor Chat pilot) so every
router wired into ReviewPipeline builds the exact same sanitized shape
instead of re-implementing it -- behavior is unchanged from the original
fyp_chat.py implementation, this is a pure relocation.
"""

from typing import Any

from app.review.models import AttemptRecord, PipelineResult


def latest_reviewer_metadata(
    history: list[AttemptRecord],
) -> tuple[str | None, str | None]:
    """
    Reviewer provider/model of the most recent AttemptRecord for which the
    Reviewer actually completed.
    """
    for record in reversed(history):
        if record.reviewed:
            return record.reviewerProvider, record.reviewerModel

    return None, None


def safe_attempt_history(history: list[AttemptRecord]) -> list[dict[str, Any]]:
    """
    Sanitized attempt-history entries for the Quality Passport / audit trail.
    Deliberately excludes ReviewerFindings and any candidate output text --
    only the same safe metadata AttemptRecord already carries for audit
    purposes (outputHash, not the candidate itself).
    """
    return [
        {
            "attemptNumber": record.attemptNumber,
            "stage": record.stage,
            "outputHash": record.outputHash,
            "firewallPassed": record.firewallPassed,
            "firewallFlags": record.firewallFlags,
            "schemaValid": record.schemaValid,
            "reviewed": record.reviewed,
            "decision": record.decision.model_dump() if record.decision else None,
            "generatorProvider": record.generatorProvider,
            "generatorModel": record.generatorModel,
            "reviewerProvider": record.reviewerProvider,
            "reviewerModel": record.reviewerModel,
            "kept": record.kept,
            "createdAt": record.createdAt.isoformat(),
        }
        for record in history
    ]


def build_review_response(result: PipelineResult) -> dict[str, Any]:
    findings = result.reviewerFindings
    reviewer_provider, reviewer_model = latest_reviewer_metadata(result.attemptHistory)

    return {
        "status": result.status,
        "usable": result.usable,
        "reviewUnavailable": result.reviewUnavailable,
        "warning": result.warning,
        "qualityScore": findings.qualityScore if findings else None,
        "strengths": findings.strengths if findings else [],
        "issues": [issue.model_dump() for issue in (findings.issues if findings else [])],
        "decisionReason": result.decision.reason if result.decision else "",
        "attempts": result.attempts,
        "reviewerVersion": result.reviewerVersion,
        "reviewRunId": result.reviewRunId,
        "reviewerProvider": reviewer_provider,
        "reviewerModel": reviewer_model,
        # Rule/category names only -- never the raw matched secret, complete
        # prompt, or discarded candidate content. See app/llm_firewall/models.py.
        "firewallInputFlags": [f.rule for f in result.firewallInputFindings],
        "firewallOutputFlags": [f.rule for f in result.firewallOutputFindings],
        "attemptHistory": safe_attempt_history(result.attemptHistory),
    }


def empty_review_response(reason: str) -> dict[str, Any]:
    """
    The 'review' object for a trivial exchange that never reached the
    pipeline at all (e.g. FypMentorAgent's short-circuit answers).
    """
    return {
        "status": "approved",
        "usable": True,
        "reviewUnavailable": False,
        "warning": "",
        "qualityScore": None,
        "strengths": [],
        "issues": [],
        "decisionReason": reason,
        "attempts": 0,
        "reviewerVersion": "n/a",
        "reviewRunId": "",
        "reviewerProvider": None,
        "reviewerModel": None,
        "firewallInputFlags": [],
        "firewallOutputFlags": [],
        "attemptHistory": [],
    }
