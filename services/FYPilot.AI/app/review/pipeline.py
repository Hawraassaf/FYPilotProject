"""
ReviewPipeline — orchestrates Writer -> Firewall -> Schema/Hard Rules ->
Reviewer -> ReviewDecisionEngine -> Rewrite, bounded and honestly labeled.

Invariants enforced here (see the approved design for the full rationale):
- The Writer is called exactly once; every further iteration goes through the
  generic RewriteAgent.
- A version is never returned merely because it passed firewall/schema, and
  never merely because "the Reviewer processed it" -- three distinct
  candidate concepts are tracked (see _PipelineState below) and a version
  with a critical issue is never shown just because it was reviewed.
- The loop never returns approved/approved_with_minor_warnings solely
  because the iteration cap was reached.
- Firewall + schema/hard-rule validation re-run on every version, including
  after each rewrite.
- A per-agent wall-clock budget bounds the total time across all attempts
  (this is a complement to, not a substitute for, per-provider timeouts --
  see app/services/llm_provider.py).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from app.llm_firewall.firewall import LlmFirewall
from app.llm_firewall.guard import GuardedCallRequest, GuardedResult, guarded_call, output_hash
from app.review.context import ReviewContext
from app.review.models import AttemptRecord, PipelineResult, ReviewerFindings, RewriteDecision
from app.review.registry import AgentReviewConfig, get_agent_config
from app.review.review_decision_engine import ReviewDecisionEngine
from app.review.reviewer_agent import ReviewerAgent
from app.review.rewrite_agent import RewriteAgent
from app.services.llm_provider import LLMResult

Candidate = dict[str, Any]
ReviewedCandidate = tuple[Candidate, ReviewerFindings]


def _safe_str(value: Any, *, max_length: int = 6000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text[:max_length]


@dataclass
class _PipelineState:
    last_structurally_valid_candidate: Candidate | None = None
    last_reviewed_noncritical_candidate: ReviewedCandidate | None = None
    last_approved_output: ReviewedCandidate | None = None


class ReviewPipeline:
    def __init__(
        self,
        agent_name: str,
        *,
        firewall: LlmFirewall | None = None,
        reviewer_agent: ReviewerAgent | None = None,
        rewrite_agent: RewriteAgent | None = None,
        decision_engine: ReviewDecisionEngine | None = None,
        config: AgentReviewConfig | None = None,
    ):
        self.agent_name = agent_name
        self.firewall = firewall or LlmFirewall()
        self.reviewer_agent = reviewer_agent or ReviewerAgent()
        self.rewrite_agent = rewrite_agent or RewriteAgent()
        self.decision_engine = decision_engine or ReviewDecisionEngine()
        self.config = config or get_agent_config(agent_name)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        writer_call_fn: Callable[[], LLMResult | None],
        context: ReviewContext,
        *,
        writer_trusted_parts: dict[str, str],
        writer_untrusted_parts: dict[str, str],
    ) -> PipelineResult:
        started_at = time.monotonic()
        review_run_id = str(uuid.uuid4())
        history: list[AttemptRecord] = []
        state = _PipelineState()

        writer_result = guarded_call(
            GuardedCallRequest(
                stage="writer",
                trusted_parts=writer_trusted_parts,
                untrusted_parts=writer_untrusted_parts,
                call_fn=writer_call_fn,
                schema=self.config.schema,
                url_mode=self.config.url_mode,
                allowed_sources=context.allowed_source_metadata,
            ),
            self.firewall,
        )

        if writer_result.provider_failed:
            return self._result(
                "provider_unavailable", usable=False, output={},
                warning="No AI provider produced an initial answer.",
                review_run_id=review_run_id, history=history, attempts=0,
            )

        if writer_result.blocked:
            return self._result(
                "firewall_blocked", usable=False, output={},
                warning="The initial answer was blocked by the content firewall.",
                review_run_id=review_run_id, history=history, attempts=0,
                firewall_findings=self._findings_of(writer_result),
            )

        version = writer_result.output or {}
        version_schema_ok = writer_result.schema_valid
        generator_provider, generator_model = writer_result.provider, writer_result.model
        produced_by_stage: str = "writer"

        attempt = 0

        while True:
            if self._time_budget_exceeded(started_at):
                return self._timeout_result(state, review_run_id, history, attempt)

            if not version_schema_ok:
                repaired_version, repaired_ok, attempt, terminal = self._attempt_structural_repair(
                    version, attempt, context, writer_trusted_parts, writer_untrusted_parts, state, review_run_id, history,
                )
                if terminal is not None:
                    return terminal

                version, version_schema_ok = repaired_version, repaired_ok
                produced_by_stage = "rewrite"
                continue

            state.last_structurally_valid_candidate = version

            reviewer_result = guarded_call(
                GuardedCallRequest(
                    stage="reviewer",
                    trusted_parts=writer_trusted_parts,
                    untrusted_parts={
                        **writer_untrusted_parts,
                        "candidate_output": _safe_str(version),
                    },
                    call_fn=lambda v=version: self.reviewer_agent.analyze(
                        v, context,
                        known_risky_claims=self.config.known_risky_claims,
                        mandatory_fields=self.config.mandatory_fields,
                    ),
                    schema=ReviewerFindings,
                ),
                self.firewall,
            )

            if reviewer_result.provider_failed or reviewer_result.blocked or not reviewer_result.schema_valid:
                return self._review_unavailable_result(state, review_run_id, history, attempt)

            findings = ReviewerFindings.model_validate(reviewer_result.output)
            has_critical = any(issue.severity == "critical" for issue in findings.issues)

            if not has_critical:
                state.last_reviewed_noncritical_candidate = (version, findings)

            decision = self.decision_engine.decide(findings, schema_ok=True)

            record = AttemptRecord(
                attemptNumber=attempt,
                stage=produced_by_stage,  # type: ignore[arg-type]
                outputHash=output_hash(version),
                firewallPassed=True,
                schemaValid=True,
                reviewed=True,
                reviewerFindings=findings,
                decision=decision,
                generatorProvider=generator_provider,
                generatorModel=generator_model,
                reviewerProvider=reviewer_result.provider,
                reviewerModel=reviewer_result.model,
                kept=False,
            )
            history.append(record)

            if not decision.requiresRewrite:
                state.last_approved_output = (version, findings)
                record.kept = True
                status = "approved" if not findings.issues else "approved_with_minor_warnings"
                return self._result(
                    status, usable=True, output=version,
                    reviewer_findings=findings, decision=decision,
                    review_run_id=review_run_id, history=history, attempts=attempt + 1,
                )

            if attempt >= self.config.max_rewrites:
                if has_critical:
                    return self._rejected_result(state, review_run_id, history, attempt, findings, decision)

                record.kept = True
                return self._result(
                    "unresolved", usable=True, output=version,
                    reviewer_findings=findings, decision=decision,
                    warning="Non-critical issues remained after the maximum number of rewrites.",
                    review_run_id=review_run_id, history=history, attempts=attempt + 1,
                )

            rewrite_result = guarded_call(
                GuardedCallRequest(
                    stage="rewrite",
                    trusted_parts=writer_trusted_parts,
                    untrusted_parts={
                        **writer_untrusted_parts,
                        "candidate_output": _safe_str(version),
                        "reviewer_findings": _safe_str([i.model_dump() for i in decision.blockingIssues]),
                    },
                    call_fn=lambda v=version, d=decision: self.rewrite_agent.rewrite(
                        v, d.blockingIssues, agent_name=self.agent_name,
                    ),
                    schema=self.config.schema,
                    url_mode=self.config.url_mode,
                    allowed_sources=context.allowed_source_metadata,
                ),
                self.firewall,
            )

            if rewrite_result.provider_failed:
                return self._review_unavailable_result(state, review_run_id, history, attempt)

            if rewrite_result.blocked:
                return self._firewall_blocked_result(
                    state, review_run_id, history, attempt, rewrite_result, stage_label="rewritten",
                )

            version = rewrite_result.output or {}
            version_schema_ok = rewrite_result.schema_valid
            generator_provider, generator_model = rewrite_result.provider, rewrite_result.model
            produced_by_stage = "rewrite"
            attempt += 1

    # ------------------------------------------------------------------
    # Structural repair (schema_invalid path)
    # ------------------------------------------------------------------

    def _attempt_structural_repair(
        self,
        version: Candidate,
        attempt: int,
        context: ReviewContext,
        writer_trusted_parts: dict[str, str],
        writer_untrusted_parts: dict[str, str],
        state: _PipelineState,
        review_run_id: str,
        history: list[AttemptRecord],
    ) -> tuple[Candidate, bool, int, PipelineResult | None]:
        if attempt >= self.config.max_rewrites:
            terminal = self._schema_invalid_result(state, review_run_id, history, attempt)
            return version, False, attempt, terminal

        fix_result = guarded_call(
            GuardedCallRequest(
                stage="rewrite",
                trusted_parts=writer_trusted_parts,
                untrusted_parts={
                    **writer_untrusted_parts,
                    "invalid_candidate": _safe_str(version),
                },
                call_fn=lambda v=version: self.rewrite_agent.fix_structure(v, agent_name=self.agent_name),
                schema=self.config.schema,
                url_mode=self.config.url_mode,
                allowed_sources=context.allowed_source_metadata,
            ),
            self.firewall,
        )

        next_attempt = attempt + 1

        if fix_result.provider_failed or fix_result.blocked:
            terminal = self._schema_invalid_result(state, review_run_id, history, next_attempt)
            return version, False, next_attempt, terminal

        history.append(
            AttemptRecord(
                attemptNumber=next_attempt,
                stage="rewrite",
                outputHash=output_hash(fix_result.output),
                firewallPassed=True,
                schemaValid=fix_result.schema_valid,
                reviewed=False,
                generatorProvider=fix_result.provider,
                generatorModel=fix_result.model,
                kept=False,
            )
        )

        return fix_result.output or {}, fix_result.schema_valid, next_attempt, None

    # ------------------------------------------------------------------
    # Terminal-result helpers -- each implements the candidate-selection
    # table from the approved design: a version is only ever shown if it is
    # last_approved_output or last_reviewed_noncritical_candidate (or, only
    # when the agent's registry entry opts in, last_structurally_valid_candidate).
    # ------------------------------------------------------------------

    def _review_unavailable_result(
        self, state: _PipelineState, review_run_id: str, history: list[AttemptRecord], attempt: int,
    ) -> PipelineResult:
        if state.last_approved_output:
            output, findings = state.last_approved_output
            return self._result(
                "review_unavailable", usable=True, output=output, reviewer_findings=findings,
                warning="A newer answer could not be verified; showing your previously approved answer.",
                review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
            )

        if state.last_reviewed_noncritical_candidate:
            output, findings = state.last_reviewed_noncritical_candidate
            return self._result(
                "review_unavailable", usable=True, output=output, reviewer_findings=findings,
                warning="A newer answer could not be verified; showing the last verified answer.",
                review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
            )

        if self.config.allow_unreviewed_output and state.last_structurally_valid_candidate:
            return self._result(
                "review_unavailable", usable=True, output=state.last_structurally_valid_candidate,
                warning="This answer passed structural checks but semantic review could not be completed.",
                review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
            )

        return self._result(
            "review_unavailable", usable=False, output={},
            warning="Semantic review could not be completed and no safe answer is available.",
            review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
        )

    def _rejected_result(
        self,
        state: _PipelineState,
        review_run_id: str,
        history: list[AttemptRecord],
        attempt: int,
        findings: ReviewerFindings,
        decision: RewriteDecision,
    ) -> PipelineResult:
        if state.last_reviewed_noncritical_candidate:
            output, prior_findings = state.last_reviewed_noncritical_candidate
            return self._result(
                "rejected", usable=True, output=output, reviewer_findings=prior_findings, decision=decision,
                warning="A critical issue remained after the maximum number of rewrites; showing the last version without a critical issue.",
                review_run_id=review_run_id, history=history, attempts=attempt + 1,
            )

        return self._result(
            "rejected", usable=False, output={}, reviewer_findings=findings, decision=decision,
            warning="A critical issue remained after the maximum number of rewrites and no safe earlier version exists.",
            review_run_id=review_run_id, history=history, attempts=attempt + 1,
        )

    def _schema_invalid_result(
        self, state: _PipelineState, review_run_id: str, history: list[AttemptRecord], attempt: int,
    ) -> PipelineResult:
        if state.last_reviewed_noncritical_candidate:
            output, findings = state.last_reviewed_noncritical_candidate
            return self._result(
                "schema_invalid", usable=True, output=output, reviewer_findings=findings,
                warning="A later version never became structurally valid; showing the last verified answer.",
                review_run_id=review_run_id, history=history, attempts=attempt,
            )

        return self._result(
            "schema_invalid", usable=False, output={},
            warning="Output never became structurally valid.",
            review_run_id=review_run_id, history=history, attempts=attempt,
        )

    def _firewall_blocked_result(
        self,
        state: _PipelineState,
        review_run_id: str,
        history: list[AttemptRecord],
        attempt: int,
        guarded: GuardedResult,
        *,
        stage_label: str,
    ) -> PipelineResult:
        if state.last_reviewed_noncritical_candidate:
            output, findings = state.last_reviewed_noncritical_candidate
            return self._result(
                "firewall_blocked", usable=True, output=output, reviewer_findings=findings,
                warning=f"The {stage_label} answer was blocked by the content firewall; showing the last verified answer.",
                review_run_id=review_run_id, history=history, attempts=attempt + 1,
                firewall_findings=self._findings_of(guarded),
            )

        return self._result(
            "firewall_blocked", usable=False, output={},
            warning=f"The {stage_label} answer was blocked by the content firewall and no safe earlier version exists.",
            review_run_id=review_run_id, history=history, attempts=attempt + 1,
            firewall_findings=self._findings_of(guarded),
        )

    def _timeout_result(
        self, state: _PipelineState, review_run_id: str, history: list[AttemptRecord], attempt: int,
    ) -> PipelineResult:
        if state.last_approved_output:
            output, findings = state.last_approved_output
            return self._result(
                "unresolved", usable=True, output=output, reviewer_findings=findings,
                warning="The review process exceeded its time budget; showing the last approved answer.",
                review_run_id=review_run_id, history=history, attempts=attempt,
            )

        if state.last_reviewed_noncritical_candidate:
            output, findings = state.last_reviewed_noncritical_candidate
            return self._result(
                "unresolved", usable=True, output=output, reviewer_findings=findings,
                warning="The review process exceeded its time budget; showing the last verified answer.",
                review_run_id=review_run_id, history=history, attempts=attempt,
            )

        return self._result(
            "review_unavailable", usable=False, output={},
            warning="The review process exceeded its time budget before completing a single review.",
            review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
        )

    # ------------------------------------------------------------------
    # Small utilities
    # ------------------------------------------------------------------

    def _time_budget_exceeded(self, started_at: float) -> bool:
        return (time.monotonic() - started_at) > self.config.max_total_seconds

    @staticmethod
    def _findings_of(guarded: GuardedResult) -> list:
        return guarded.verdict.findings if guarded.verdict else []

    def _result(
        self,
        status: str,
        *,
        usable: bool,
        output: Candidate,
        review_run_id: str,
        history: list[AttemptRecord],
        attempts: int,
        reviewer_findings: ReviewerFindings | None = None,
        decision: RewriteDecision | None = None,
        warning: str = "",
        review_unavailable: bool = False,
        firewall_findings: list | None = None,
    ) -> PipelineResult:
        return PipelineResult(
            status=status,  # type: ignore[arg-type]
            usable=usable,
            output=output or {},
            reviewUnavailable=review_unavailable,
            warning=warning,
            reviewerFindings=reviewer_findings,
            decision=decision,
            attempts=attempts,
            attemptHistory=history,
            reviewRunId=review_run_id,
            firewallOutputFindings=firewall_findings or [],
        )
