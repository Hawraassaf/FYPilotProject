"""Focused offline regression checks for the SE Documentation rejection gate."""

from __future__ import annotations

import json

from app.review.models import ReviewerFindings, ReviewerIssue, RewriteDecision
from app.review.pipeline import ReviewPipeline, _PipelineState


def test_legacy_diagnostic_flag_cannot_make_critical_rejection_usable(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_DOCUMENTATION_CRITICAL_GATE_DISABLED", "1")
    monkeypatch.setenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", str(tmp_path))

    candidate = {"projectTitle": "Rejected candidate", "projectOverview": "Unverified"}
    issue = ReviewerIssue(
        severity="critical",
        requiresCorrection=True,
        category="project_alignment",
        affectedField="projectOverview",
        description="The candidate contradicts trusted project context.",
        revisionInstruction="Regenerate from the trusted project evidence.",
    )
    findings = ReviewerFindings(issues=[issue], qualityScore=20)
    decision = RewriteDecision(
        requiresRewrite=True,
        reason="A critical project-alignment issue remains unresolved.",
        blockingIssues=[issue],
        highestBlockingSeverity="critical",
    )
    state = _PipelineState(last_structurally_valid_candidate=candidate)
    pipeline = object.__new__(ReviewPipeline)
    pipeline.agent_name = "SEDocumentationAgent"

    result = pipeline._rejected_result(
        state,
        review_run_id="review-run-1",
        history=[],
        attempt=1,
        findings=findings,
        decision=decision,
    )

    assert result.status == "rejected"
    assert result.usable is False
    assert result.displayable is False
    assert result.output == {}

    snapshots = list(tmp_path.glob("se_documentation_candidate_*.json"))
    assert len(snapshots) == 1
    assert json.loads(snapshots[0].read_text(encoding="utf-8")) == candidate
