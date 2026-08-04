"""
Coordinated SE-Documentation-specific timeout correction (see chat for full
rationale): DeepInfra per-call timeout for SE Documentation is 180s (a real
~6500-token section was measured live at 121s -- the previous 60s "standard"
tier default was cutting every section off before it could finish); the
Python-side total deadline (orchestrator section budget + ReviewPipeline's
max_total_seconds) is 960s, coordinated as ONE absolute deadline computed
once by the router and threaded through unchanged; the .NET HttpClient
timeout for this one endpoint is 1020s so .NET never abandons a request
while Python is still legitimately working on it.

Every test here is scoped to SE Documentation only -- see the
"other agents retain their existing values" tests at the bottom, which
snapshot every other tier/agent's timeout to prove nothing else moved.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import BaseModel

from app.agents.se_documentation.project_facts import build_project_facts
from app.agents.se_documentation.se_documentation_orchestrator import (
    SEDocumentationOrchestratorAgent,
    SEDocumentationRequest,
)
from app.review.pipeline import ReviewPipeline
from app.review.registry import AgentReviewConfig, get_agent_config
from app.services.llm_provider import (
    DeepInfraProvider,
    ProviderChain,
    _deepinfra_model_for_tier,
    _deepinfra_timing_for_tier,
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


def test_se_documentation_registry_deadline_is_960_seconds():
    config = get_agent_config("SEDocumentationAgent")
    assert config.max_total_seconds == 960.0


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
    generate()/generate_candidate() must forward the router's ONE deadline
    into section generation unchanged -- not recompute a fresh
    now + _SECTIONS_TIME_BUDGET_SECONDS window that could silently extend
    past what ReviewPipeline itself is enforcing.
    """
    agent = SEDocumentationOrchestratorAgent()
    facts = build_project_facts(SEDocumentationRequest())
    external_deadline = 12345.0

    with patch.object(agent, "_call_section_safe", return_value=None) as fake_call:
        agent._generate_llm_sections(
            SEDocumentationRequest(),
            facts,
            deadline=external_deadline,
        )

    assert agent._sections_deadline == external_deadline
    assert fake_call.called


def test_sections_deadline_is_the_same_object_across_every_section_call(monkeypatch):
    """
    Each section call checks time.monotonic() against self._sections_deadline
    (see _call_section_safe) -- this proves that value never changes across
    the run, i.e. no stage silently gets a fresh budget.
    """
    agent = SEDocumentationOrchestratorAgent()
    facts = build_project_facts(SEDocumentationRequest())
    external_deadline = 500.0

    seen_deadlines: list[float] = []

    def fake_call_section_safe(key, prompt, max_tokens):
        seen_deadlines.append(agent._sections_deadline)
        return None

    with patch.object(agent, "_call_section_safe", side_effect=fake_call_section_safe):
        agent._generate_llm_sections(
            SEDocumentationRequest(),
            facts,
            deadline=external_deadline,
        )

    assert len(seen_deadlines) >= 6  # requirements, useCases, modules, database, uiApi, testingSecurity, (aiReport)
    assert all(d == external_deadline for d in seen_deadlines)


def test_no_external_deadline_falls_back_to_960_second_budget(monkeypatch):
    """Direct/test callers that don't pass a deadline keep working exactly as
    before -- a fresh 960s window starting now."""
    monkeypatch.setattr(
        "app.agents.se_documentation.se_documentation_orchestrator.time.monotonic",
        lambda: 1000.0,
    )

    agent = SEDocumentationOrchestratorAgent()
    facts = build_project_facts(SEDocumentationRequest())

    with patch.object(agent, "_call_section_safe", return_value=None):
        agent._generate_llm_sections(SEDocumentationRequest(), facts, deadline=None)

    assert agent._sections_deadline == 1000.0 + 960.0


# ---------------------------------------------------------------------------
# 4. A timeout saves no fallback.
# ---------------------------------------------------------------------------


def test_all_sections_timing_out_produces_llm_used_false_not_a_disguised_success():
    """
    When every section is skipped because the deadline was already spent,
    generate() must honestly report last_llm_used=False so the .NET
    acceptance gate (IsRealAiOutput) rejects it -- never assembled/labeled
    as if a real provider produced it. See
    DocumentationGeneratorServiceTests.LlmUsedFalse_IsRejected_NoPersistence
    (tests/FYPilot.Tests) for the .NET-side enforcement of this contract.
    """
    agent = SEDocumentationOrchestratorAgent()
    already_expired_deadline = time.monotonic() - 1.0  # in the past

    result = agent.generate(
        SEDocumentationRequest(),
        deadline=already_expired_deadline,
    )

    assert agent.last_llm_used is False
    assert all(status == "fallback" for status in agent.section_provenance.values())
    assert result.projectTitle  # deterministic assembly still runs -- just never claims to be LLM output


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
    assert get_agent_config("ProjectRoadmapAgent").max_total_seconds == 90.0
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
