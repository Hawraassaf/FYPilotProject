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
"""

import pytest

from app.review.pipeline import ReviewPipeline, _PipelineState
from app.review.registry import get_agent_config


@pytest.mark.parametrize("agent_name", ["SEDocumentationAgent"])
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
    """Step 2 is intentionally scoped; it does not globally weaken review."""

    assert get_agent_config("FypMentorAgent").allow_unreviewed_output is False
    assert get_agent_config("ProjectRoadmapAgent").allow_unreviewed_output is False
    # Regression guard: this was silently flipped to True alongside an
    # unrelated max_rewrites refactor and has been reverted -- see the
    # module docstring.
    assert get_agent_config("ProjectDNAAgent").allow_unreviewed_output is False
