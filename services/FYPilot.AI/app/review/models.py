"""
Data models for the semantic review pipeline (Reviewer / ReviewDecisionEngine /
Rewrite / ReviewPipeline). Pure data only — no behavior, no LLM calls, no
firewall logic. See review_decision_engine.py, reviewer_agent.py, pipeline.py.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app.llm_firewall.models import FirewallFinding

Severity = Literal["critical", "high", "medium", "low"]


class ReviewerIssue(BaseModel):
    """
    One problem the semantic Reviewer found in a candidate output.

    requiresCorrection is the Reviewer's own explicit judgment call on whether
    this issue is material enough to need a fix — the deterministic
    ReviewDecisionEngine trusts this flag instead of inferring materiality from
    a raw count of medium-severity issues.
    """

    severity: Severity
    requiresCorrection: bool
    category: str
    affectedField: str
    description: str
    revisionInstruction: str


class ReviewerFindings(BaseModel):
    strengths: list[str] = Field(default_factory=list)
    issues: list[ReviewerIssue] = Field(default_factory=list)
    qualityScore: int = 0
    overallAssessment: str = ""


class RewriteDecision(BaseModel):
    """
    Output of the deterministic ReviewDecisionEngine. Never produced by an LLM.
    """

    requiresRewrite: bool
    reason: str
    blockingIssues: list[ReviewerIssue] = Field(default_factory=list)
    highestBlockingSeverity: Severity | None = None


PipelineStatus = Literal[
    "approved",
    "approved_with_minor_warnings",
    "unresolved",
    "rejected",
    "firewall_blocked",
    "review_unavailable",
    "provider_unavailable",
    "schema_invalid",
    # Statuses below are specific to IdeaComparisonAgent's decoupled router
    # flow (see routers/idea_comparison.py) rather than ReviewPipeline.run()
    # -- that flow calls IdeaComparisonAgent.compare() directly (one writer
    # call, deterministic validation) and only conditionally runs the
    # semantic Reviewer, so it needs a status vocabulary that distinguishes
    # "never attempted because review was decoupled from rendering" from
    # ReviewPipeline's own "attempted but timed out" (review_unavailable).
    "automated_checks_passed",  # deterministic checks passed; review skipped (insufficient deadline time to attempt it)
    "review_pending",  # reserved for a future async-after-response Reviewer call; not yet emitted (see idea_comparison.py)
    "reviewed",  # Reviewer ran synchronously and found no blocking issue
    "review_rejected",  # Reviewer ran synchronously and found a blocking issue
    # Statuses below are specific to the job-based Idea Comparison worker's
    # rewrite-on-rejection flow (app/jobs/workers/idea_comparison_worker.py)
    # -- distinct from the synchronous /compare-generated-ideas endpoint's
    # vocabulary above, which is left completely unchanged.
    "approved_after_revision",  # first review rejected; one rewrite attempted using the reviewer's own RevisionInstructions; second review approved it
    "review_rejected_safe_fallback",  # rejected and no usable rewrite was possible (no actionable feedback, or the rewrite was itself rejected again) -- safe fallback shown, never a second rewrite attempt
    "rewrite_unavailable_deadline",  # first review rejected, a rewrite was warranted, but fewer than 25s remained in the job's global deadline -- safe fallback shown
    "rewrite_provider_unavailable",  # first review rejected, a rewrite was attempted, but every provider failed during the rewrite call -- safe fallback shown
]


class AttemptRecord(BaseModel):
    """
    Hash-based audit entry for one attempt (the initial Writer draft or a
    Rewrite). Never stores the full candidate text, only a hash of it — the
    final displayed answer is already stored elsewhere (e.g. ChatMessage for
    Mentor Chat); this trail exists for auditing discarded candidates too.
    """

    attemptNumber: int
    stage: Literal["writer", "rewrite"]
    outputHash: str
    firewallPassed: bool
    firewallFlags: list[str] = Field(default_factory=list)
    schemaValid: bool
    reviewed: bool
    reviewerFindings: ReviewerFindings | None = None
    decision: RewriteDecision | None = None
    generatorProvider: str | None = None
    generatorModel: str | None = None
    reviewerProvider: str | None = None
    reviewerModel: str | None = None
    kept: bool
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PipelineResult(BaseModel):
    """
    The full result of a ReviewPipeline run — this IS the AI Quality Passport
    returned to .NET and persisted to ai_output_reviews.
    """

    status: PipelineStatus
    usable: bool
    output: dict
    reviewUnavailable: bool = False
    warning: str = ""
    reviewerFindings: ReviewerFindings | None = None
    decision: RewriteDecision | None = None
    attempts: int = 0
    attemptHistory: list[AttemptRecord] = Field(default_factory=list)
    reviewerVersion: str = "review-pipeline-v1"
    reviewRunId: str = ""
    firewallInputFindings: list[FirewallFinding] = Field(default_factory=list)
    firewallOutputFindings: list[FirewallFinding] = Field(default_factory=list)
