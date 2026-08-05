from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from app.review.context import ReviewContext
from app.review.models import ReviewerIssue, RewriteDecision
from app.review.pipeline import ReviewPipeline
from app.review.registry import AgentReviewConfig
from app.review.rewrite_agent import RewriteAgent


class MinimalCandidate(BaseModel):
    summary: str


class FakeProviderChain:
    def __init__(self) -> None:
        self.last_prompt = ""

    def generate_json(self, prompt: str, *, use_search: bool = False):
        self.last_prompt = prompt
        return {"prompt": prompt, "use_search": use_search}


def _issue() -> ReviewerIssue:
    return ReviewerIssue(
        severity="high",
        requiresCorrection=True,
        category="contradiction",
        affectedField="summary",
        description="The summary describes FYPilot instead of the selected project.",
        revisionInstruction="Rewrite it using the selected project's actual purpose.",
    )


def _context() -> ReviewContext:
    return ReviewContext(
        agent_name="SEDocumentationAgent",
        trusted_system_instructions="Document the selected target application only.",
        trusted_structural_context={
            "ideaId": 42,
            "teamSize": 1,
            "availableHoursPerWeek": 20,
        },
        untrusted_project_text={
            "projectTitle": "Arabic Medical Symptom Triage Assistant",
            "problemStatement": "Arabic-speaking users need safer symptom urgency guidance.",
            "technologyStack": "ASP.NET Core, Python FastAPI, PostgreSQL",
        },
        untrusted_user_input="Generate software engineering documentation for this project.",
        untrusted_conversation_history=["The target is a medical triage prototype."],
    )


def test_semantic_rewrite_prompt_contains_full_project_context() -> None:
    fake_chain = FakeProviderChain()
    agent = RewriteAgent(fake_chain)  # type: ignore[arg-type]

    result = agent.rewrite(
        {"summary": "FYPilot generates documentation."},
        [_issue()],
        _context(),
        agent_name="SEDocumentationAgent",
    )

    prompt = fake_chain.last_prompt
    assert result["use_search"] is False
    assert "Arabic Medical Symptom Triage Assistant" in prompt
    assert '"ideaId": 42' in prompt
    assert "Document the selected target application only." in prompt
    assert "Generate software engineering documentation for this project." in prompt
    assert "The target is a medical triage prototype." in prompt
    assert 'AFFECTED FIELDS REPORTED BY THE REVIEWER (DATA):\n["summary"]' in prompt


def test_semantic_rewrite_prompt_prevents_host_platform_contamination() -> None:
    fake_chain = FakeProviderChain()
    agent = RewriteAgent(fake_chain)  # type: ignore[arg-type]

    agent.rewrite(
        {"summary": "FYPilot generates documentation."},
        [_issue()],
        _context(),
        agent_name="SEDocumentationAgent",
    )

    prompt = fake_chain.last_prompt
    assert "Do not substitute the host FYPilot platform's pages" in prompt
    assert "documentation generator" in prompt
    assert "unless those items are explicitly part of the supplied target" in prompt
    assert "Do not guess missing student skills" in prompt


class TwoStepDecisionEngine:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, findings, *, schema_ok: bool):
        self.calls += 1
        if self.calls == 1:
            return RewriteDecision(
                requiresRewrite=True,
                reason="A verified contradiction requires correction.",
                blockingIssues=findings.issues,
                highestBlockingSeverity="high",
            )
        return RewriteDecision(
            requiresRewrite=False,
            reason="No blocking issues remain.",
            blockingIssues=[],
            highestBlockingSeverity=None,
        )


class SpyRewriteAgent:
    def __init__(self) -> None:
        self.received_context: ReviewContext | None = None

    def rewrite(self, candidate, blocking_issues, context, *, agent_name):
        self.received_context = context
        return SimpleNamespace()

    def rewrite_targeted(self, candidate, closure, blocking_issues, context, *, agent_name, schema_cls, deadline=None):
        self.received_context = context
        return SimpleNamespace()


def _guarded_result(*, output, schema_valid=True):
    return SimpleNamespace(
        provider_failed=False,
        blocked=False,
        schema_valid=schema_valid,
        output=output,
        provider="test-provider",
        model="test-model",
    )


def test_pipeline_forwards_same_review_context_to_semantic_rewrite(monkeypatch) -> None:
    reviewer_calls = 0
    spy = SpyRewriteAgent()
    context = _context()

    def fake_guarded_call(request, _firewall):
        nonlocal reviewer_calls

        if request.stage == "writer":
            return _guarded_result(output={"summary": "Wrong host-platform summary."})

        if request.stage == "reviewer":
            reviewer_calls += 1
            if reviewer_calls == 1:
                return _guarded_result(
                    output={
                        "strengths": [],
                        "issues": [_issue().model_dump()],
                        "qualityScore": 60,
                        "overallAssessment": "One contradiction remains.",
                    }
                )
            return _guarded_result(
                output={
                    "strengths": ["The target project is now correctly described."],
                    "issues": [],
                    "qualityScore": 95,
                    "overallAssessment": "Approved.",
                }
            )

        if request.stage == "rewrite" and "reviewer_findings" in request.untrusted_parts:
            request.call_fn()
            return _guarded_result(output={"summary": "Medical triage project summary."})

        raise AssertionError(f"Unexpected guarded call: {request.stage}")

    monkeypatch.setattr("app.review.pipeline.guarded_call", fake_guarded_call)

    pipeline = ReviewPipeline(
        "SEDocumentationAgent",
        firewall=object(),
        reviewer_agent=object(),
        rewrite_agent=spy,  # type: ignore[arg-type]
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
        context,
        writer_trusted_parts={"system": "trusted"},
        writer_untrusted_parts={"input": "untrusted"},
    )

    assert result.status == "approved"
    assert result.output == {"summary": "Medical triage project summary."}
    assert spy.received_context is context
