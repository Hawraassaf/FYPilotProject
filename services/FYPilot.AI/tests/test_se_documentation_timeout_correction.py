"""
Coordinated SE-Documentation-specific timeout correction (see chat for full
rationale): DeepInfra per-call timeout for SE Documentation is 180s (a real
~6500-token section was measured live at 121s -- the previous 60s "standard"
tier default was cutting every section off before it could finish).

Second correction (bounded-concurrency + reserved-review-budget): a live
end-to-end run with the ORIGINAL single-960s-deadline design measured the
Writer stage alone (7 sequential sections) taking ~967s -- already past the
960s deadline before ReviewPipeline's semantic Reviewer was ever called.
The fix: Python's global deadline is now 1200s, split into a 900s Writer
deadline (global - 300s reserved for semantic review/rewrite) enforced by
an ordered bounded queue (max 2 concurrent section calls -- see
se_documentation_orchestrator.py's _generate_llm_sections), and the
original, unmodified 1200s global deadline is what actually reaches
ReviewPipeline. The .NET HttpClient timeout for this one endpoint is 1260s
so .NET never abandons a request while Python is still legitimately
working on it.

Every test here is scoped to SE Documentation only -- see the
"other agents retain their existing values" tests at the bottom, which
snapshot every other tier/agent's timeout to prove nothing else moved.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from app.agents.se_documentation.project_facts import build_project_facts
from app.agents.se_documentation.se_documentation_orchestrator import (
    SEDocumentationOrchestratorAgent,
    SEDocumentationRequest,
    WriterBudgetExceededError,
)
from app.review.pipeline import ReviewPipeline
from app.review.registry import AgentReviewConfig, get_agent_config
from app.agents.se_documentation.se_documentation_orchestrator import SectionCallResult
from app.services.llm_provider import (
    DeepInfraProvider,
    ProviderChain,
    _deepinfra_model_for_tier,
    _deepinfra_timing_for_tier,
)


def _ok_result(key: str, launch_order: int) -> SectionCallResult:
    """Minimal successful SectionCallResult for tests that only care about
    deadline propagation / queue mechanics, not real section content."""
    return SectionCallResult(
        section_key=key, launch_order=launch_order, success=True, data={"ok": True},
        provider="fake", model="fake-model", provenance="provider",
        error_code=None, error_message=None,
        start_time=0.0, end_time=0.1, duration=0.1,
        configured_timeout=180.0, effective_timeout=180.0,
        remaining_writer_budget_at_start=900.0,
    )


# ---------------------------------------------------------------------------
# 1. A provider response taking 121 seconds does not time out.
# ---------------------------------------------------------------------------


def test_se_documentation_deepinfra_timeout_covers_measured_121s_latency():
    timing = _deepinfra_timing_for_tier("se_documentation")

    # Measured live against the real DeepInfra API for a ~6500-token
    # section on meta-llama/Llama-3.3-70B-Instruct-Turbo: 121s. 180s leaves
    # real margin instead of a near-miss.
    assert timing["timeout_seconds"] == 180.0
    assert timing["timeout_seconds"] > 121.0
    # No SDK auto-retries -- a genuine timeout should fail into Groq once,
    # not have the SDK silently re-attempt (and re-pay for) the same slow
    # call two more times first.
    assert timing["max_retries"] == 0


def test_se_documentation_timeout_is_actually_wired_into_the_openai_client():
    """
    Not just present in a config dict -- actually passed to the real
    OpenAI-compatible client DeepInfraProvider constructs, so a 121s
    response genuinely would not be cut off client-side.
    """
    provider = DeepInfraProvider(
        model=_deepinfra_model_for_tier("se_documentation"),
        max_retries=_deepinfra_timing_for_tier("se_documentation").get("max_retries"),
        timeout_seconds=_deepinfra_timing_for_tier("se_documentation").get("timeout_seconds"),
    )

    captured = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    with patch("openai.OpenAI", _FakeOpenAI):
        provider._client()

    assert captured["timeout"] == 180.0
    assert captured["max_retries"] == 0


def test_se_documentation_provider_chain_uses_se_documentation_tier():
    chain = ProviderChain(tier="se_documentation")
    deepinfra = chain.providers[0]

    assert deepinfra.timeout_seconds == 180.0
    assert deepinfra.max_retries == 0
    # Model unchanged from the earlier cost-driven move off "high" --
    # this correction is about timing only, not the model choice.
    assert deepinfra.model == "meta-llama/Llama-3.3-70B-Instruct-Turbo"


# ---------------------------------------------------------------------------
# 2. The total SE deadline is enforced.
# ---------------------------------------------------------------------------


def test_se_documentation_registry_deadline_is_1200_seconds():
    config = get_agent_config("SEDocumentationAgent")
    assert config.max_total_seconds == 1200.0


class _MinimalSchema(BaseModel):
    value: str = ""


def test_pipeline_times_out_once_the_shared_deadline_passes(monkeypatch):
    """
    Once time.monotonic() has passed the deadline, the very first check at
    the top of the review loop must return a timeout result immediately --
    before ever touching structural repair, the Reviewer, or Rewrite. This
    is what "check remaining time before starting each new
    section/review operation" actually enforces at the pipeline level.
    """
    # started_at=100.0 -> deadline=100.0+960.0=1060.0; the loop's very first
    # _time_budget_exceeded check then sees 1200.0 > 1060.0 and stops clean.
    times = iter([100.0, 1200.0])
    monkeypatch.setattr("app.review.pipeline.time.monotonic", lambda: next(times))

    def fake_guarded_call(request, _firewall):
        assert request.stage == "writer"
        return SimpleNamespace(
            provider_failed=False,
            blocked=False,
            schema_valid=False,  # irrelevant -- the deadline check fires first
            output={},
            provider="test-provider",
            model="test-model",
            input_verdict=None,
            output_verdict=None,
        )

    monkeypatch.setattr("app.review.pipeline.guarded_call", fake_guarded_call)

    pipeline = ReviewPipeline(
        "SEDocumentationAgent",
        firewall=object(),
        reviewer_agent=object(),
        rewrite_agent=object(),
        config=AgentReviewConfig(
            schema=_MinimalSchema,
            allow_unreviewed_output=True,
            max_total_seconds=960.0,
        ),
    )

    from app.review.context import ReviewContext

    result = pipeline.run(
        lambda: None,
        ReviewContext(agent_name="SEDocumentationAgent"),
        writer_trusted_parts={},
        writer_untrusted_parts={},
    )

    assert result.usable is False
    assert result.output == {}
    assert result.displayable is False


# ---------------------------------------------------------------------------
# 3. The deadline is not reset between sections.
# ---------------------------------------------------------------------------


def test_sections_deadline_uses_the_externally_supplied_deadline_verbatim():
    """
    generate()/generate_candidate() must forward the router's WRITER
    deadline into section generation unchanged -- not recompute a fresh
    now + _SECTIONS_TIME_BUDGET_SECONDS window that could silently extend
    past what the router itself computed.
    """
    agent = SEDocumentationOrchestratorAgent()
    facts = build_project_facts(SEDocumentationRequest())
    external_deadline = time.monotonic() + 12345.0

    with patch.object(agent, "_call_section_concurrent_safe", return_value=_ok_result("x", 1)) as fake_call:
        agent._generate_llm_sections(
            SEDocumentationRequest(),
            facts,
            deadline=external_deadline,
        )

    assert agent._sections_deadline == external_deadline
    assert fake_call.called


def test_sections_deadline_is_the_same_object_across_every_section_call():
    """
    Every section call receives the SAME writer_deadline value as an
    explicit argument (never re-derived from self.* on a worker thread --
    see _call_section_concurrent_safe's thread-safety contract) -- this
    proves that value never changes across the run, i.e. no section
    silently gets a fresh budget.
    """
    agent = SEDocumentationOrchestratorAgent()
    facts = build_project_facts(SEDocumentationRequest())
    external_deadline = time.monotonic() + 500.0

    seen_deadlines: list[float] = []

    def fake_call_section_concurrent_safe(key, prompt, max_tokens, writer_deadline, launch_order):
        seen_deadlines.append(writer_deadline)
        return _ok_result(key, launch_order)

    with patch.object(agent, "_call_section_concurrent_safe", side_effect=fake_call_section_concurrent_safe):
        agent._generate_llm_sections(
            SEDocumentationRequest(),
            facts,
            deadline=external_deadline,
        )

    assert len(seen_deadlines) >= 6  # requirements, useCases, modules, database, uiApi, testingSecurity, (aiReport)
    assert all(d == external_deadline for d in seen_deadlines)


def test_no_external_deadline_falls_back_to_900_second_budget(monkeypatch):
    """Direct/test callers that don't pass a deadline keep working -- a
    fresh 900s window starting now (matches the real production writer
    budget: global_deadline 1200s - the 300s semantic-review reserve)."""
    monkeypatch.setattr(
        "app.agents.se_documentation.se_documentation_orchestrator.time.monotonic",
        lambda: 1000.0,
    )

    agent = SEDocumentationOrchestratorAgent()
    facts = build_project_facts(SEDocumentationRequest())

    with patch.object(agent, "_call_section_concurrent_safe", return_value=_ok_result("x", 1)):
        agent._generate_llm_sections(SEDocumentationRequest(), facts, deadline=None)

    assert agent._sections_deadline == 1000.0 + 900.0


# ---------------------------------------------------------------------------
# 4. A timeout saves no fallback.
# ---------------------------------------------------------------------------


def test_all_sections_timing_out_produces_writer_budget_exceeded_not_a_disguised_success():
    """
    When the Writer deadline has ALREADY passed before a single section
    could even be attempted, generate() must raise WriterBudgetExceededError
    -- never silently assemble and return a full-fallback document as if
    that were a normal, acceptable outcome. This is a deliberate behavior
    change from the previous "last_llm_used=False, still returns a
    document" contract: the stabilization task's explicit requirement is
    "do not use core deterministic fallback for missing core sections; do
    not assemble an incomplete document" when the Writer budget is
    exhausted before every section could be attempted. See
    DocumentationGeneratorServiceTests (tests/FYPilot.Tests) for the
    .NET-side WriterBudgetExceeded handling this feeds into.
    """
    agent = SEDocumentationOrchestratorAgent()
    already_expired_deadline = time.monotonic() - 1.0  # in the past

    with pytest.raises(WriterBudgetExceededError) as exc_info:
        agent.generate(
            SEDocumentationRequest(),
            deadline=already_expired_deadline,
        )

    assert set(exc_info.value.missing_sections) == {
        "requirements", "useCases", "modulesArchitecture", "database", "uiApi", "testingSecurity",
    }
    assert exc_info.value.completed_sections == []


# ---------------------------------------------------------------------------
# 5. Previous valid documentation remains unchanged.
# ---------------------------------------------------------------------------
#
# This is a .NET/database-layer guarantee (Python has no notion of "the
# previously persisted document"). Covered by:
#   tests/FYPilot.Tests/Documentation/DocumentationGeneratorServiceTests.cs
#   ::FailedRegeneration_LeavesExistingDocumentUntouched
# which asserts a failed regeneration (including a timeout-shaped
# HttpRequestException) leaves the prior row and prior AiOutputReview intact.


# ---------------------------------------------------------------------------
# 6. Other agents retain their existing timeout values.
# ---------------------------------------------------------------------------


def test_other_agents_registry_deadlines_are_unchanged():
    assert get_agent_config("FypMentorAgent").max_total_seconds == 90.0
    # ProjectRoadmapAgent is intentionally 240.0 now, not 90.0 -- see the
    # Roadmap-only timeout adjustment in registry.py and
    # tests/test_roadmap_timeout_adjustment.py.
    assert get_agent_config("ProjectRoadmapAgent").max_total_seconds == 240.0
    assert get_agent_config("ProjectIdeaAgent").max_total_seconds == 120.0
    assert get_agent_config("ProjectDNAAgent").max_total_seconds == 90.0
    assert get_agent_config("IdeaComparisonAgent").max_total_seconds == 45.0


def test_other_deepinfra_tiers_are_unchanged():
    # "standard" (market needs, project DNA, market footprint): still no
    # override -- DeepInfraProvider's own 60s/SDK-default-retries apply.
    assert _deepinfra_timing_for_tier("standard") == {}
    # "high" (Project Roadmap, Idea Generator): untouched.
    assert _deepinfra_model_for_tier("high") == "anthropic/claude-opus-4-8"
    # "roadmap": untouched (120s default, max_retries=0, its own env var).
    roadmap_timing = _deepinfra_timing_for_tier("roadmap")
    assert roadmap_timing["timeout_seconds"] == 120.0
    assert roadmap_timing["max_retries"] == 0
    # "comparison"/"comparison_review": untouched.
    assert _deepinfra_timing_for_tier("comparison") == {"timeout_seconds": 34.0, "max_retries": 0}
    assert _deepinfra_timing_for_tier("comparison_review") == {"timeout_seconds": 50.0, "max_retries": 0}
    # "mentor"/"light": model defaults untouched.
    assert _deepinfra_model_for_tier("mentor") == "Qwen/Qwen3-Coder-480B-A35B-Instruct-Turbo"
    assert _deepinfra_model_for_tier("light") == "google/gemma-3-12b-it"


def test_reviewpipeline_run_without_a_deadline_keeps_computing_its_own(monkeypatch):
    """
    Every OTHER agent calls pipeline.run(...) without the new `deadline`
    keyword -- confirms that path is byte-for-byte the previous behavior
    (fresh started_at + config.max_total_seconds), not silently altered by
    this correction.
    """
    monkeypatch.setattr("app.review.pipeline.time.monotonic", lambda: 200.0)

    captured_deadlines: list[float] = []

    def fake_guarded_call(request, _firewall):
        if request.stage == "writer":
            return SimpleNamespace(
                provider_failed=True, blocked=False, schema_valid=False,
                output={}, provider=None, model=None,
                input_verdict=None, output_verdict=None,
            )
        raise AssertionError("should not reach reviewer/rewrite")

    monkeypatch.setattr("app.review.pipeline.guarded_call", fake_guarded_call)

    pipeline = ReviewPipeline(
        "FypMentorAgent",
        firewall=object(),
        reviewer_agent=object(),
        rewrite_agent=object(),
        config=AgentReviewConfig(schema=SimpleNamespace, max_total_seconds=90.0),
    )

    from app.review.context import ReviewContext

    result = pipeline.run(
        lambda: None,
        ReviewContext(agent_name="FypMentorAgent"),
        writer_trusted_parts={},
        writer_untrusted_parts={},
    )

    assert result.status == "provider_unavailable"
