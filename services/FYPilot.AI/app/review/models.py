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
