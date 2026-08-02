from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from app.review.context import ReviewContext
from app.review.models import RewriteDecision
from app.review.pipeline import ReviewPipeline
from app.review.registry import AgentReviewConfig
from app.review.rewrite_agent import RewriteAgent


class MinimalCandidate(BaseModel):
    value: str


def _guarded_result(*, output, schema_valid=True):
    return SimpleNamespace(
        provider_failed=False,
        blocked=False,
        schema_valid=schema_valid,
        output=output,
        provider="test-provider",
        model="test-model",
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


class TwoStepDecisionEngine:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, findings, *, schema_ok: bool):
        self.calls += 1
        if self.calls == 1:
            return RewriteDecision(
                requiresRewrite=True,
                reason="One verified contradiction requires correction.",
                blockingIssues=findings.issues,
                highestBlockingSeverity="high",
            )
        return RewriteDecision(
            requiresRewrite=False,
            reason="No blocking issues remain.",
            blockingIssues=[],
            highestBlockingSeverity=None,
        )


class SpyReviewerAgent:
    def __init__(self) -> None:
        self.deadlines: list[float] = []

    def analyze(
        self,
        candidate,
        context,
        *,
        known_risky_claims,
        mandatory_fields,
        extra_rubric,
        deadline=None,
    ):
        self.deadlines.append(deadline)
        return SimpleNamespace()


class SpyRewriteAgent:
    def __init__(self) -> None:
        self.semantic_deadlines: list[float] = []
        self.structural_deadlines: list[float] = []

    def rewrite(self, candidate, blocking_issues, context, *, agent_name, deadline=None):
        self.semantic_deadlines.append(deadline)
        return SimpleNamespace()

    def fix_structure(
        self,
        candidate,
        *,
        agent_name,
        validation_errors,
        expected_schema,
        deadline=None,
    ):
        self.structural_deadlines.append(deadline)
        return SimpleNamespace()


def test_pipeline_passes_one_absolute_deadline_to_reviewer_and_semantic_rewrite(monkeypatch):
    reviewer = SpyReviewerAgent()
    rewrite = SpyRewriteAgent()
    reviewer_calls = 0

    monkeypatch.setattr("app.review.pipeline.time.monotonic", lambda: 100.0)

    def fake_guarded_call(request, _firewall):
        nonlocal reviewer_calls

        if request.stage == "writer":
            return _guarded_result(output={"value": "wrong"})

        if request.stage == "reviewer":
            request.call_fn()
            reviewer_calls += 1
            return _guarded_result(
                output=_blocking_findings() if reviewer_calls == 1 else _approved_findings()
            )

        if request.stage == "rewrite" and "reviewer_findings" in request.untrusted_parts:
            request.call_fn()
            return _guarded_result(output={"value": "corrected"})

        raise AssertionError(f"Unexpected guarded call: {request.stage}")

    monkeypatch.setattr("app.review.pipeline.guarded_call", fake_guarded_call)

    pipeline = ReviewPipeline(
        "TestAgent",
        firewall=object(),
        reviewer_agent=reviewer,  # type: ignore[arg-type]
        rewrite_agent=rewrite,  # type: ignore[arg-type]
        decision_engine=TwoStepDecisionEngine(),
        config=AgentReviewConfig(
            schema=MinimalCandidate,
            max_structural_repairs=1,
            max_semantic_rewrites=1,
            allow_unreviewed_output=True,
            max_total_seconds=25.0,
        ),
    )

    result = pipeline.run(
        lambda: None,
        ReviewContext(agent_name="TestAgent"),
        writer_trusted_parts={"system": "trusted"},
        writer_untrusted_parts={"input": "untrusted"},
    )

    assert result.status == "approved"
    assert result.output == {"value": "corrected"}
    assert reviewer.deadlines == [125.0, 125.0]
    assert rewrite.semantic_deadlines == [125.0]


def test_pipeline_passes_deadline_to_structural_repair(monkeypatch):
    reviewer = SpyReviewerAgent()
    rewrite = SpyRewriteAgent()

    monkeypatch.setattr("app.review.pipeline.time.monotonic", lambda: 50.0)

    def fake_guarded_call(request, _firewall):
        if request.stage == "writer":
            return _guarded_result(output={"value": 123}, schema_valid=False)

        if request.stage == "rewrite" and "invalid_candidate" in request.untrusted_parts:
            request.call_fn()
            return _guarded_result(output={"value": "fixed"}, schema_valid=True)

        if request.stage == "reviewer":
            request.call_fn()
            return _guarded_result(output=_approved_findings())

        raise AssertionError(f"Unexpected guarded call: {request.stage}")

    monkeypatch.setattr("app.review.pipeline.guarded_call", fake_guarded_call)

    pipeline = ReviewPipeline(
        "TestAgent",
        firewall=object(),
        reviewer_agent=reviewer,  # type: ignore[arg-type]
        rewrite_agent=rewrite,  # type: ignore[arg-type]
        config=AgentReviewConfig(
            schema=MinimalCandidate,
            allow_unreviewed_output=True,
            max_total_seconds=40.0,
        ),
    )

    result = pipeline.run(
        lambda: None,
        ReviewContext(agent_name="TestAgent"),
        writer_trusted_parts={"system": "trusted"},
        writer_untrusted_parts={"input": "untrusted"},
    )

    assert result.status == "approved"
    assert result.output == {"value": "fixed"}
    assert rewrite.structural_deadlines == [90.0]
    assert reviewer.deadlines == [90.0]


class DeadlineAwareProvider:
    def __init__(self) -> None:
        self.deadline = None

    def generate_json(self, prompt, *, use_search=False, deadline=None):
        self.deadline = deadline
        return {"ok": True}


def test_rewrite_agent_forwards_deadline_to_provider_chain():
    provider = DeadlineAwareProvider()
    agent = RewriteAgent(provider)  # type: ignore[arg-type]

    result = agent.fix_structure(
        {"value": 123},
        agent_name="TestAgent",
        validation_errors=[{"location": "value", "message": "wrong type"}],
        expected_schema={"type": "object"},
        deadline=321.5,
    )

    assert result == {"ok": True}
    assert provider.deadline == 321.5


def test_writer_result_is_preserved_when_budget_expires_before_review(monkeypatch):
    times = iter([100.0, 120.0])
    monkeypatch.setattr("app.review.pipeline.time.monotonic", lambda: next(times))

    def fake_guarded_call(request, _firewall):
        assert request.stage == "writer"
        return _guarded_result(output={"value": "paid-valid-output"})

    monkeypatch.setattr("app.review.pipeline.guarded_call", fake_guarded_call)

    pipeline = ReviewPipeline(
        "TestAgent",
        firewall=object(),
        reviewer_agent=object(),
        rewrite_agent=object(),
        config=AgentReviewConfig(
            schema=MinimalCandidate,
            allow_unreviewed_output=True,
            max_total_seconds=10.0,
        ),
    )

    result = pipeline.run(
        lambda: None,
        ReviewContext(agent_name="TestAgent"),
        writer_trusted_parts={"system": "trusted"},
        writer_untrusted_parts={"input": "untrusted"},
    )

    assert result.status == "review_unavailable"
    assert result.usable is True
    assert result.output == {"value": "paid-valid-output"}
    assert result.reviewUnavailable is True
