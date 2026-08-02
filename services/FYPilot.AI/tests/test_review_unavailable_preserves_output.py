"""Regression tests for Step 2: preserve structurally valid DNA/SE output.

Place this file at:
    tests/review/test_review_unavailable_preserves_output.py
"""

import pytest

from app.review.pipeline import ReviewPipeline, _PipelineState
from app.review.registry import get_agent_config


@pytest.mark.parametrize("agent_name", ["ProjectDNAAgent", "SEDocumentationAgent"])
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
