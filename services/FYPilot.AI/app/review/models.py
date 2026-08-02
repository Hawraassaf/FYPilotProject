"""
Data models for the semantic review pipeline (Reviewer / ReviewDecisionEngine /
Rewrite / ReviewPipeline). Pure data only — no behavior, no LLM calls, no
firewall logic. See review_decision_engine.py, reviewer_agent.py, pipeline.py.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.llm_firewall.models import FirewallFinding

Severity = Literal["critical", "high", "medium", "low"]
AttemptOperation = Literal["writer", "structural_repair", "semantic_rewrite"]
AttemptOutcome = Literal["candidate_produced", "provider_failed", "firewall_blocked"]
OutputOrigin = Literal["writer", "structural_repair", "semantic_rewrite", "unknown", "none"]
OutputReviewLevel = Literal["approved", "reviewed_with_warnings", "structural_only", "none"]


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
    Output of a deterministic decision step. Never produced by an LLM.

    Produced by either the shared ReviewDecisionEngine (every agent except
    the job-based Idea Comparison path below) or, for that one path only,
    app/jobs/workers/idea_comparison_worker.py's own
    _classify_idea_comparison_issues -- a deliberately separate, looser
    policy (see that function's docstring) that never touches
    ReviewDecisionEngine itself, so no other agent is affected.
    """

    requiresRewrite: bool
    reason: str
    blockingIssues: list[ReviewerIssue] = Field(default_factory=list)
    # Only ever populated by the Idea Comparison job worker's own
    # classification -- issues that didn't qualify as a validated blocker
    # (wrong severity, category not on the closed blocking allowlist, or
    # requiresCorrection=false) but are still surfaced to the student
    # instead of being silently dropped. Always empty for every other
    # caller of RewriteDecision (the shared ReviewDecisionEngine never sets
    # this field).
    warningIssues: list[ReviewerIssue] = Field(default_factory=list)
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
    "approved_after_revision",  # first review rejected; one rewrite attempted using the reviewer's own RevisionInstructions; second review approved it (no warnings either)
    "review_rejected_safe_fallback",  # rejected and no usable rewrite was possible (no actionable feedback, or the rewrite was itself rejected again) -- safe fallback shown, never a second rewrite attempt
    "rewrite_unavailable_deadline",  # first review rejected, a rewrite was warranted, but insufficient time remained in the job's global deadline -- safe fallback shown
    "rewrite_provider_unavailable",  # first review rejected, a rewrite was attempted, but every provider failed during the rewrite call -- safe fallback shown
    # Statuses below are specific to the job worker's own looser blocking
    # policy (_classify_idea_comparison_issues) -- a comparison is approved
    # as long as it has no VALIDATED high/critical blocker, even if the
    # Reviewer raised lower-severity or off-allowlist issues; those are
    # surfaced as warnings rather than discarding a good comparison.
    "approved_with_warnings",  # first review found no blocking issue, but did find at least one non-blocking warning
    "approved_after_revision_with_warnings",  # first review blocked; one rewrite attempted; second review found no blocking issue but at least one non-blocking warning
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
    operation: AttemptOperation = "writer"
    outcome: AttemptOutcome = "candidate_produced"
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
    # Consumer contract: render output whenever displayable/usable is true.
    # Status explains how the run ended; it is not itself a visibility gate.
    displayable: bool = False
    outputOrigin: OutputOrigin = "none"
    outputReviewLevel: OutputReviewLevel = "none"
    reviewerFindings: ReviewerFindings | None = None
    decision: RewriteDecision | None = None
    attempts: int = 0
    attemptHistory: list[AttemptRecord] = Field(default_factory=list)
    reviewerVersion: str = "review-pipeline-v2"
    reviewRunId: str = ""
    firewallInputFindings: list[FirewallFinding] = Field(default_factory=list)
    firewallOutputFindings: list[FirewallFinding] = Field(default_factory=list)


    @model_validator(mode="after")
    def _enforce_delivery_contract(self) -> "PipelineResult":
        has_output = bool(self.output)

        if self.usable and not has_output:
            raise ValueError("usable=True requires a non-empty output")
        if not self.usable and has_output:
            raise ValueError("usable=False requires an empty output")

        # Keep the old `usable` field for backward compatibility while adding
        # an unambiguous UI-facing name. Both always mean the same thing.
        self.displayable = self.usable

        if not self.usable:
            self.outputOrigin = "none"
            self.outputReviewLevel = "none"

        return self