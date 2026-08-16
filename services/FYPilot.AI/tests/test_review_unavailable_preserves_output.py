"""Regression tests for preserving structurally valid SE Documentation output.

Place this file at:
    tests/review/test_review_unavailable_preserves_output.py

ProjectDNAAgent is deliberately NOT covered here: allow_unreviewed_output was
briefly True for DNA (in the same commit that split max_rewrites into
max_structural_repairs/max_semantic_rewrites) but has been reverted to its
original False -- DNA's short, single-call 90s budget doesn't share SE
Documentation's long-timeout risk profile that justifies opting into this
behavior, and DNA's rubric explicitly checks for skill-rating contradictions
that must never reach the student unreviewed. See registry.py's
ProjectDNAAgent entry for the current reasoning.

FypMentorAgent was ALSO flipped to True (deliberately, not accidentally --
see registry.py's FypMentorAgent entry for the full reasoning): a Reviewer
outage was discarding a Writer candidate that had already passed both
firewall passes and full schema validation, replacing it with a generic
templated fallback. This is a genuine review-POLICY fix, kept intentionally
separate from the provider-reliability fix (see llm_provider.py's "mentor"
tier timing / reviewer_agent.py's cap_timeout_to_deadline) that reduces how
often the Reviewer stage fails in the first place -- ReviewDecisionEngine's
actual rejection rubric is completely unaffected by this flag either way.

ProjectIdeaAgent (Idea Generator) was flipped to True for the identical
reason, as part of the pre-freeze production-hardening pass (see registry.py's
ProjectIdeaAgent entry): its Reviewer/Rewrite stages share the SAME "high"
tier as its Writer, inside only a ~30s review reserve carved out of the 120s
total budget (routers/ideas.py's _WRITER_TIME_RESERVE_SECONDS), after the
Writer's own two sequential calls (search then generation) may have already
used most of the remaining time -- a narrower, more exposed window than any
other agent using this flag. Without it, a transient Reviewer outage
discarded a real, personalized, live-search-grounded 4-idea batch for the
generic _fallback_raw_ideas() templates, even though that batch had already
passed url_mode="no_urls_allowed" and full schema validation.

MarketFootprintAgent ("Regional Demand Footprint" / "Yearly Intelligence",
embedded in the Idea Generator page's "Refresh Insight" button) was flipped
to True for the same reason, as part of its own dedicated pre-freeze
hardening pass (see registry.py's MarketFootprintAgent entry): its Writer
stage also makes two sequential calls (search then generation) inside a 150s
total budget with only a 30s review reserve, and its output was already
protected by url_mode="source_metadata_only" plus the Gap 1 URL-stripping
fix (market_footprint_agent.py) by the time this flag matters.
"""

import pytest

from app.review.pipeline import ReviewPipeline, _PipelineState
from app.review.registry import get_agent_config


@pytest.mark.parametrize(
    "agent_name",
    ["SEDocumentationAgent", "FypMentorAgent", "ProjectIdeaAgent", "MarketFootprintAgent"],
)
def test_reviewer_failure_preserves_structurally_valid_output(agent_name: str) -> None:
    """A semantic Reviewer outage must not erase a valid paid generation."""

    pipeline = ReviewPipeline.__new__(ReviewPipeline)
    pipeline.config = get_agent_config(agent_name)

    candidate = {
        "marker": f"valid-{agent_name}",
        "summary": "Structurally valid generated output.",
    }
    state = _PipelineState(last_structurally_valid_candidate=candidate)

    result = pipeline._review_unavailable_result(
        state=state,
        review_run_id="test-review-run",
        history=[],
        attempt=0,
        guarded=None,
    )

    assert result.status == "review_unavailable"
    assert result.reviewUnavailable is True
    assert result.usable is True
    assert result.output == candidate
    assert "passed structural checks" in result.warning


def test_sensitive_agents_do_not_all_become_unreviewed_by_default() -> None:
    """This flag is intentionally scoped per-agent; it does not globally weaken review."""

    assert get_agent_config("ProjectRoadmapAgent").allow_unreviewed_output is False
    # Regression guard: this was silently flipped to True alongside an
    # unrelated max_rewrites refactor and has been reverted -- see the
    # module docstring.
    assert get_agent_config("ProjectDNAAgent").allow_unreviewed_output is False
    # FypMentorAgent is True -- a deliberate, documented opt-in (see the
    # module docstring above and registry.py's FypMentorAgent entry), NOT an
    # accidental flip like the DNA regression this test otherwise guards
    # against.
    assert get_agent_config("FypMentorAgent").allow_unreviewed_output is True
    # ProjectIdeaAgent is ALSO True -- the Gap 3 fix from the pre-freeze
    # production-hardening pass (see the module docstring above and
    # registry.py's ProjectIdeaAgent entry), likewise a deliberate,
    # documented opt-in.
    assert get_agent_config("ProjectIdeaAgent").allow_unreviewed_output is True
    # MarketFootprintAgent is ALSO True -- its own dedicated pre-freeze
    # hardening pass (see the module docstring above and registry.py's
    # MarketFootprintAgent entry).
    assert get_agent_config("MarketFootprintAgent").allow_unreviewed_output is True
    # Regression guard: this fix must stay scoped to the agents that
    # explicitly opted in and must never silently spread to sibling agents
    # that haven't -- MarketNeedsAgent uses the exact same two-call
    # search-then-generate shape as MarketFootprintAgent but was
    # deliberately NOT part of this task's scope.
    assert get_agent_config("MarketNeedsAgent").allow_unreviewed_output is False
