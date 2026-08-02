from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from app.review.context import ReviewContext
from app.review.models import PipelineResult, RewriteDecision
from app.review.pipeline import ReviewPipeline
from app.review.registry import AgentReviewConfig
from app.review.response import build_review_response


class MinimalCandidate(BaseModel):
    value: str


def _guarded_result(
    *,
    output=None,
    schema_valid=True,
    provider_failed=False,
    blocked=False,
):
    return SimpleNamespace(
        provider_failed=provider_failed,
        blocked=blocked,
        schema_valid=schema_valid,
        output=output,
        provider="test-provider",
        model="test-model",
        input_verdict=None,
        output_verdict=None,
    )


def _approved_findings():
    return {
        "strengths": ["Grounded and complete."],
        "issues": [],
        "qualityScore": 95,
        "overallAssessment": "Approved.",
    }


def _blocking_findings():
    return {
        "strengths": [],
        "issues": [
            {
                "severity": "high",
                "requiresCorrection": True,
                "category": "contradiction",
                "affectedField": "value",
                "description": "The value contradicts trusted context.",
                "revisionInstruction": "Replace it with the trusted value.",
            }
        ],
        "qualityScore": 55,
        "overallAssessment": "One material issue remains.",
    }


class OneRewriteDecisionEngine:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, findings, *, schema_ok: bool):
        self.calls += 1
        if self.calls == 1:
            return RewriteDecision(
                requiresRewrite=True,
                reason="One material contradiction requires correction.",
                blockingIssues=findings.issues,
                highestBlockingSeverity="high",
            )
        return RewriteDecision(
            requiresRewrite=False,
            reason="No blocking issues remain.",
            blockingIssues=[],
            highestBlockingSeverity=None,
        )


def test_writer_attempt_is_recorded_before_reviewer_failure(monkeypatch):
    calls = 0

    def fake_guarded_call(request, _firewall):
        nonlocal calls
        calls += 1
        if request.stage == "writer":
            return _guarded_result(output={"value": "paid-writer-output"})
        if request.stage == "reviewer":
            return _guarded_result(provider_failed=True)
        raise AssertionError(f"Unexpected stage: {request.stage}")

    monkeypatch.setattr("app.review.pipeline.guarded_call", fake_guarded_call)

    pipeline = ReviewPipeline(
        "TestAgent",
        firewall=object(),
        reviewer_agent=object(),
        rewrite_agent=object(),
        config=AgentReviewConfig(
            schema=MinimalCandidate,
            allow_unreviewed_output=True,
        ),
    )

    result = pipeline.run(
        lambda: None,
        ReviewContext(agent_name="TestAgent"),
        writer_trusted_parts={"system": "trusted"},
        writer_untrusted_parts={"input": "untrusted"},
    )

    assert calls == 2
    assert result.status == "review_unavailable"
    assert result.usable is True
    assert result.displayable is True
    assert result.output == {"value": "paid-writer-output"}
    assert result.outputOrigin == "writer"
    assert result.outputReviewLevel == "structural_only"

    assert len(result.attemptHistory) == 1
    record = result.attemptHistory[0]
    assert record.operation == "writer"
    assert record.reviewed is False
    assert record.schemaValid is True
    assert record.kept is True


def test_semantic_rewrite_attempt_is_audited_even_when_second_review_fails(monkeypatch):
    reviewer_calls = 0

    def fake_guarded_call(request, _firewall):
        nonlocal reviewer_calls

        if request.stage == "writer":
            return _guarded_result(output={"value": "original"})

        if request.stage == "reviewer":
            reviewer_calls += 1
            if reviewer_calls == 1:
                return _guarded_result(output=_blocking_findings())
            return _guarded_result(provider_failed=True)

        if request.stage == "rewrite" and "reviewer_findings" in request.untrusted_parts:
            return _guarded_result(output={"value": "rewritten"})

        raise AssertionError(f"Unexpected stage: {request.stage}")

    monkeypatch.setattr("app.review.pipeline.guarded_call", fake_guarded_call)

    pipeline = ReviewPipeline(
        "TestAgent",
        firewall=object(),
        reviewer_agent=object(),
        rewrite_agent=object(),
        decision_engine=OneRewriteDecisionEngine(),
        config=AgentReviewConfig(
            schema=MinimalCandidate,
            max_structural_repairs=1,
            max_semantic_rewrites=1,
            allow_unreviewed_output=True,
        ),
    )

    result = pipeline.run(
        lambda: None,
        ReviewContext(agent_name="TestAgent"),
        writer_trusted_parts={"system": "trusted"},
        writer_untrusted_parts={"input": "untrusted"},
    )

    assert [record.operation for record in result.attemptHistory] == [
        "writer",
        "semantic_rewrite",
    ]
    assert result.attemptHistory[0].reviewed is True
    assert result.attemptHistory[1].reviewed is False
    # The pipeline conservatively displays the last reviewed non-critical
    # version when the rewritten version could not be reviewed.
    assert result.output == {"value": "original"}
    assert result.outputOrigin == "writer"
    assert result.outputReviewLevel == "reviewed_with_warnings"
    assert result.displayable is True


def test_pipeline_result_rejects_usable_empty_output():
    with pytest.raises(ValidationError):
        PipelineResult(
            status="review_unavailable",
            usable=True,
            output={},
        )


def test_review_response_exposes_unambiguous_delivery_contract(monkeypatch):
    def fake_guarded_call(request, _firewall):
        if request.stage == "writer":
            return _guarded_result(output={"value": "kept"})
        if request.stage == "reviewer":
            return _guarded_result(output=_approved_findings())
        raise AssertionError(f"Unexpected stage: {request.stage}")

    monkeypatch.setattr("app.review.pipeline.guarded_call", fake_guarded_call)

    pipeline = ReviewPipeline(
        "TestAgent",
        firewall=object(),
        reviewer_agent=object(),
        rewrite_agent=object(),
        config=AgentReviewConfig(schema=MinimalCandidate),
    )

    result = pipeline.run(
        lambda: None,
        ReviewContext(agent_name="TestAgent"),
        writer_trusted_parts={"system": "trusted"},
        writer_untrusted_parts={"input": "untrusted"},
    )
    review = build_review_response(result)

    assert review["status"] == "approved"
    assert review["usable"] is True
    assert review["displayable"] is True
    assert review["outputOrigin"] == "writer"
    assert review["outputReviewLevel"] == "approved"
    assert review["attemptHistory"][0]["operation"] == "writer"
    assert review["attemptHistory"][0]["outcome"] == "candidate_produced"
