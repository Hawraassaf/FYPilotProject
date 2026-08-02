from types import SimpleNamespace

from pydantic import BaseModel

from app.review.context import ReviewContext
from app.review.models import RewriteDecision
from app.review.pipeline import ReviewPipeline
from app.review.registry import AgentReviewConfig


class MinimalCandidate(BaseModel):
    value: str


class TwoStepDecisionEngine:
    """Request one semantic rewrite, then approve the rewritten candidate."""

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, findings, *, schema_ok: bool):
        self.calls += 1
        if self.calls == 1:
            return RewriteDecision(
                requiresRewrite=True,
                reason="One material issue requires correction.",
                blockingIssues=findings.issues,
                highestBlockingSeverity="high",
            )

        return RewriteDecision(
            requiresRewrite=False,
            reason="No blocking issues remain.",
            blockingIssues=[],
            highestBlockingSeverity=None,
        )


def _guarded_result(*, output, schema_valid=True):
    return SimpleNamespace(
        provider_failed=False,
        blocked=False,
        schema_valid=schema_valid,
        output=output,
        provider="test-provider",
        model="test-model",
    )


def test_structural_repair_does_not_consume_semantic_rewrite(monkeypatch):
    events: list[str] = []
    reviewer_calls = 0

    def fake_guarded_call(request, _firewall):
        nonlocal reviewer_calls

        if request.stage == "writer":
            events.append("writer")
            return _guarded_result(
                output={"value": "writer-draft"},
                schema_valid=False,
            )

        if request.stage == "rewrite" and "invalid_candidate" in request.untrusted_parts:
            events.append("structural-repair")
            return _guarded_result(output={"value": "structure-fixed"})

        if request.stage == "reviewer":
            reviewer_calls += 1
            events.append(f"reviewer-{reviewer_calls}")

            if reviewer_calls == 1:
                return _guarded_result(
                    output={
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
                        "qualityScore": 60,
                        "overallAssessment": "One material issue remains.",
                    }
                )

            return _guarded_result(
                output={
                    "strengths": ["The material issue was corrected."],
                    "issues": [],
                    "qualityScore": 95,
                    "overallAssessment": "Approved.",
                }
            )

        if request.stage == "rewrite" and "reviewer_findings" in request.untrusted_parts:
            events.append("semantic-rewrite")
            return _guarded_result(output={"value": "semantic-fixed"})

        raise AssertionError(
            f"Unexpected guarded call: stage={request.stage}, "
            f"parts={sorted(request.untrusted_parts)}"
        )

    monkeypatch.setattr("app.review.pipeline.guarded_call", fake_guarded_call)

    pipeline = ReviewPipeline(
        "TestAgent",
        firewall=object(),
        reviewer_agent=object(),
        rewrite_agent=object(),
        decision_engine=TwoStepDecisionEngine(),
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

    assert result.status == "approved"
    assert result.usable is True
    assert result.output == {"value": "semantic-fixed"}
    assert events == [
        "writer",
        "structural-repair",
        "reviewer-1",
        "semantic-rewrite",
        "reviewer-2",
    ]


def test_legacy_max_rewrites_remains_backward_compatible():
    config = AgentReviewConfig(schema=MinimalCandidate, max_rewrites=2)

    assert config.max_structural_repairs == 2
    assert config.max_semantic_rewrites == 2
