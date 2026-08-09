"""
Tests for the per-primary-section semantic rewrite queue -- the semantic-
rewrite counterpart to test_se_documentation_section_repair_queue.py's
structural-repair queue. Converts "one rewrite_targeted call spanning every
blocking reviewer finding" into "one bounded rewrite_targeted call per
primary section (or tightly-coupled group of sections one finding names
together)" -- see se_documentation_rewrite_scope.group_blocking_issues_by_
primary_sections and pipeline.py's SE-Documentation-only semantic rewrite
branch.
"""

from __future__ import annotations

from app.review.context import ReviewContext
from app.review.models import ReviewerIssue
from app.review.pipeline import ReviewPipeline
from app.review.se_documentation_rewrite_scope import (
    group_blocking_issues_by_primary_sections,
)
from app.services.llm_provider import LLMResult


def _issue(field: str, description: str = "issue description") -> ReviewerIssue:
    return ReviewerIssue(
        severity="high",
        requiresCorrection=True,
        category="contradiction",
        affectedField=field,
        description=description,
        revisionInstruction="Correct this using the supplied project context.",
    )


def _candidate(**overrides) -> dict:
    from app.agents.se_documentation.se_documentation_orchestrator import (
        SEDocSelectedIdea,
        SEDocumentationOrchestratorAgent,
        SEDocumentationRequest,
    )

    agent = SEDocumentationOrchestratorAgent()
    base = agent.build_safe_fallback(
        SEDocumentationRequest(selectedIdea=SEDocSelectedIdea(title="Arabic Medical Symptom Triage Assistant"))
    ).model_dump()
    base.update(overrides)
    return base


def _context() -> ReviewContext:
    return ReviewContext(
        agent_name="SEDocumentationAgent",
        trusted_system_instructions="SEDocumentationAgent test context.",
        trusted_structural_context={"teamSize": 2, "experienceLevel": "intermediate"},
        untrusted_project_text={"ideaTitle": "Arabic Medical Symptom Triage Assistant"},
    )


def _llm_ok(data, provider="deepinfra", model="test-model"):
    return LLMResult(ok=True, provider=provider, model=model, text="", data=data)


class _FakeRecordingRewriteAgent:
    """Implements only rewrite_targeted -- records one call's closure per
    group, in call order."""

    def __init__(self, responses_by_root: dict[frozenset, LLMResult]):
        self._responses = responses_by_root
        self.call_log: list[frozenset] = []

    def rewrite_targeted(self, candidate, closure, blocking_issues, context, *, agent_name, schema_cls, deadline=None, max_tokens_override=None):
        self.call_log.append(closure.primary_sections)
        return self._responses[closure.primary_sections]


class _FakeReviewerFirstPassFlagsThenClean:
    def __init__(self, issues):
        self._issues = issues
        self.calls = 0

    def analyze(self, candidate, context, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _llm_ok({
                "strengths": [], "qualityScore": 40,
                "overallAssessment": "issues found",
                "issues": [i.model_dump() for i in self._issues],
            })
        return _llm_ok({"strengths": [], "issues": [], "qualityScore": 95, "overallAssessment": "fine"})


def test_semantic_rewrite_grouping_is_deterministic_and_one_call_per_group():
    candidate = _candidate()

    issues = [
        _issue("projectOverview", "Overview is vague."),
        _issue("databaseEntities", "Contains an unrelated entity."),
    ]

    groups = group_blocking_issues_by_primary_sections(candidate, issues)
    assert [key for key, _ in groups] == sorted([key for key, _ in groups], key=lambda r: sorted(r))
    assert {"projectOverview"} in [set(k) for k, _ in groups]
    assert {"databaseEntities"} in [set(k) for k, _ in groups]
    assert len(groups) == 2


def test_pipeline_issues_one_rewrite_call_per_primary_section_group():
    from app.review.registry import get_agent_config

    candidate = _candidate()
    issues = [
        _issue("projectOverview", "Overview is vague."),
        _issue("databaseEntities", "Contains an unrelated entity."),
    ]

    rewrite_agent = _FakeRecordingRewriteAgent({
        frozenset({"projectOverview"}): _llm_ok({"projectOverview": "A clear, specific overview."}),
        frozenset({"databaseEntities"}): _llm_ok({"databaseEntities": candidate["databaseEntities"]}),
    })

    pipeline = ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=_FakeReviewerFirstPassFlagsThenClean(issues),
        rewrite_agent=rewrite_agent,
        config=get_agent_config("SEDocumentationAgent"),
    )

    result = pipeline.run(
        lambda: _llm_ok(candidate),
        _context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert len(rewrite_agent.call_log) == 2
    assert rewrite_agent.call_log == sorted(rewrite_agent.call_log, key=lambda r: sorted(r))
    assert result.status == "approved"
    assert result.output["projectOverview"] == "A clear, specific overview."


def test_one_failed_group_does_not_prevent_the_other_group_from_being_applied():
    from app.review.registry import get_agent_config

    candidate = _candidate()
    issues = [
        _issue("projectOverview", "Overview is vague."),
        _issue("databaseEntities", "Contains an unrelated entity."),
    ]

    class _PartiallyFailingRewriteAgent:
        def __init__(self):
            self.call_log = []

        def rewrite_targeted(self, candidate, closure, blocking_issues, context, *, agent_name, schema_cls, deadline=None, max_tokens_override=None):
            self.call_log.append(closure.primary_sections)
            if closure.primary_sections == frozenset({"databaseEntities"}):
                return LLMResult(ok=False, provider="none", model=None, text="", data=None, error="provider failed")
            return _llm_ok({"projectOverview": "A clear, specific overview."})

    rewrite_agent = _PartiallyFailingRewriteAgent()

    pipeline = ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=_FakeReviewerFirstPassFlagsThenClean(issues),
        rewrite_agent=rewrite_agent,
        config=get_agent_config("SEDocumentationAgent"),
    )

    result = pipeline.run(
        lambda: _llm_ok(candidate),
        _context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    # projectOverview's successful rewrite is preserved even though
    # databaseEntities's rewrite failed in the same pass.
    assert result.output["projectOverview"] == "A clear, specific overview."
    assert len(rewrite_agent.call_log) == 2
