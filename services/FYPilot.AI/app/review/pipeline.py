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

import inspect
import json
import logging
import os
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
from app.review.schema_validation import validate_detailed
from app.review.se_documentation_rewrite_scope import (
    ScopeResolutionError,
    build_compact_rewrite_candidate,
    merge_targeted_rewrite,
    resolve_rewrite_closure,
)
from app.review.se_documentation_structural_repair_scope import (
    StructuralRepairPayloadTooLargeError,
    StructuralRepairScopeError,
    estimate_tokens,
    merge_structural_repair,
    resolve_structural_repair_plan,
    restore_never_llm_rewritable_fields,
)
from app.review.section_scope import apply_scoped_rewrite, revision_scope_for
from app.services.llm_provider import LLMResult, ProviderChain

logger = logging.getLogger("fypilot-review-pipeline")

Candidate = dict[str, Any]
ReviewedCandidate = tuple[Candidate, ReviewerFindings]

_SE_DOCUMENTATION_AGENT_NAME = "SEDocumentationAgent"


def _safe_str(value: Any, *, max_length: int = 6000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text[:max_length]


def _accepts_keyword(callable_obj: Callable[..., Any], keyword: str) -> bool:
    """Return whether a callable accepts ``keyword`` without invoking it.

    Real ReviewerAgent/RewriteAgent implementations accept ``deadline``.  This
    compatibility check keeps older custom test doubles and integrations that
    have not yet added the optional keyword working without a risky
    try-call/retry pattern that could duplicate a paid provider request.
    """
    try:
        parameters = inspect.signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        # Some extension/builtin callables do not expose a signature. Prefer
        # forwarding the keyword so the production deadline is not silently
        # dropped.
        return True

    return any(
        parameter.name == keyword or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _invoke_with_deadline(
    callable_obj: Callable[..., Any],
    *args: Any,
    deadline: float,
    **kwargs: Any,
) -> Any:
    if _accepts_keyword(callable_obj, "deadline"):
        kwargs["deadline"] = deadline
    return callable_obj(*args, **kwargs)


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
        tier: str = "standard",
    ):
        # tier picks the DeepInfra model (see ProviderChain / see
        # _DEEPINFRA_TIER_DEFAULTS) for the Reviewer/Rewrite stages -- it
        # should match the Writer agent's own tier, since reviewing/fixing
        # SE Documentation's high-stakes output deserves the same accuracy
        # as generating it, while reviewing Defense Simulator's short
        # answers doesn't need a high-tier model. Ignored when
        # reviewer_agent/rewrite_agent are passed explicitly.
        self.agent_name = agent_name
        self.firewall = firewall or LlmFirewall()
        self.reviewer_agent = reviewer_agent or ReviewerAgent(ProviderChain(tier=tier))
        self.rewrite_agent = rewrite_agent or RewriteAgent(ProviderChain(tier=tier))
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
        deadline: float | None = None,
    ) -> PipelineResult:
        """
        ``deadline`` is an absolute time.monotonic() timestamp. When the
        caller supplies one (SE Documentation's router does, so the SAME
        deadline also governs its Writer-stage section generation -- see
        SEDocumentationOrchestratorAgent.generate()), it is used verbatim:
        this method never recomputes or resets it. When omitted (every other
        agent, unchanged), a fresh deadline is computed here from
        self.config.max_total_seconds exactly as before.
        """
        started_at = time.monotonic()
        deadline = deadline if deadline is not None else started_at + self.config.max_total_seconds
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
                firewall_input_findings=self._input_findings_of(writer_result),
                firewall_output_findings=self._output_findings_of(writer_result),
            )

        version = writer_result.output or {}
        version_schema_ok = writer_result.schema_valid
        generator_provider, generator_model = writer_result.provider, writer_result.model

        # Preserve an already-paid, structurally valid Writer response before
        # checking the wall-clock budget. If the Writer consumed the remaining
        # budget, the timeout path can still return this valid candidate instead
        # of collapsing to an empty output.
        if version_schema_ok:
            state.last_structurally_valid_candidate = version

        # Record the paid Writer candidate immediately. Previously the Writer
        # attempt was added only after semantic review completed, so a Reviewer
        # outage made attemptHistory look empty even though generation had run.
        self._append_candidate_record(
            history,
            attempt_number=0,
            operation="writer",
            candidate=version,
            guarded=writer_result,
        )

        # attempt remains the total version-attempt counter used by the
        # existing audit/result contract. The two counters below enforce
        # independent budgets: a structural JSON/schema repair must not use
        # up the semantic content-rewrite allowance.
        attempt = 0
        structural_repairs = 0
        semantic_rewrites = 0

        while True:
            if self._time_budget_exceeded(deadline):
                return self._timeout_result(state, review_run_id, history, attempt)

            if not version_schema_ok:
                repaired_version, repaired_ok, attempt, structural_repairs, terminal = self._attempt_structural_repair(
                    version,
                    attempt,
                    structural_repairs,
                    context,
                    writer_trusted_parts,
                    writer_untrusted_parts,
                    state,
                    review_run_id,
                    history,
                    deadline,
                )
                if terminal is not None:
                    return terminal

                version, version_schema_ok = repaired_version, repaired_ok
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
                    call_fn=lambda v=version: _invoke_with_deadline(
                        self.reviewer_agent.analyze,
                        v,
                        context,
                        known_risky_claims=self.config.known_risky_claims,
                        mandatory_fields=self.config.mandatory_fields,
                        extra_rubric=self.config.extra_rubric,
                        deadline=deadline,
                    ),
                    schema=ReviewerFindings,
                ),
                self.firewall,
            )

            if reviewer_result.provider_failed or reviewer_result.blocked or not reviewer_result.schema_valid:
                return self._review_unavailable_result(
                    state, review_run_id, history, attempt, guarded=reviewer_result,
                )

            findings = ReviewerFindings.model_validate(reviewer_result.output)
            has_critical = any(issue.severity == "critical" for issue in findings.issues)

            if not has_critical:
                state.last_reviewed_noncritical_candidate = (version, findings)

            decision = self.decision_engine.decide(findings, schema_ok=True)

            record = self._mark_candidate_reviewed(
                history,
                candidate=version,
                findings=findings,
                decision=decision,
                reviewer_result=reviewer_result,
                fallback_attempt_number=attempt,
                fallback_generator_provider=generator_provider,
                fallback_generator_model=generator_model,
            )

            if not decision.requiresRewrite:
                state.last_approved_output = (version, findings)
                record.kept = True
                status = "approved" if not findings.issues else "approved_with_minor_warnings"
                return self._result(
                    status, usable=True, output=version,
                    reviewer_findings=findings, decision=decision,
                    review_run_id=review_run_id, history=history, attempts=attempt + 1,
                )

            if semantic_rewrites >= self.config.max_semantic_rewrites:
                if has_critical:
                    return self._rejected_result(state, review_run_id, history, attempt, findings, decision)

                record.kept = True
                return self._result(
                    "unresolved", usable=True, output=version,
                    reviewer_findings=findings, decision=decision,
                    warning="Non-critical issues remained after the maximum number of rewrites.",
                    review_run_id=review_run_id, history=history, attempts=attempt + 1,
                )

            if self.agent_name == _SE_DOCUMENTATION_AGENT_NAME:
                # SE Documentation-only path: send only the reviewer-confirmed
                # sections (row-filtered where possible) instead of the
                # complete document -- see se_documentation_rewrite_scope.py's
                # module docstring for the ~30,000-token full-document
                # rewrite request this replaces. Every other agent falls
                # through to the unchanged full-object path below.
                try:
                    closure = resolve_rewrite_closure(version, decision.blockingIssues)
                except ScopeResolutionError as exc:
                    logger.warning("se_documentation.rewrite_scope_resolution_failed reason=%s", exc)
                    return self._review_unavailable_result(
                        state, review_run_id, history, attempt,
                    )

                allowed_rewrite_sections = sorted(closure.allowed_sections)

                rewrite_result = guarded_call(
                    GuardedCallRequest(
                        stage="rewrite",
                        trusted_parts=writer_trusted_parts,
                        untrusted_parts={
                            **writer_untrusted_parts,
                            "candidate_output": _safe_str(build_compact_rewrite_candidate(version, closure)),
                            "reviewer_findings": _safe_str([i.model_dump() for i in decision.blockingIssues]),
                            "allowed_rewrite_sections": _safe_str(allowed_rewrite_sections),
                        },
                        # No `schema=` here on purpose: the raw LLM response is
                        # a PARTIAL object (only the allowed sections), which
                        # would fail full-SEDocumentationDto validation even
                        # when perfectly correct. The merged, complete
                        # candidate is schema-validated below instead (same
                        # validate_detailed call the full-object path uses).
                        call_fn=lambda v=version, c=closure, d=decision: _invoke_with_deadline(
                            self.rewrite_agent.rewrite_targeted,
                            v,
                            c,
                            d.blockingIssues,
                            context,
                            agent_name=self.agent_name,
                            schema_cls=self.config.schema,
                            deadline=deadline,
                        ),
                        url_mode=self.config.url_mode,
                        allowed_sources=context.allowed_source_metadata,
                    ),
                    self.firewall,
                )

                if rewrite_result.provider_failed:
                    return self._review_unavailable_result(
                        state, review_run_id, history, attempt, guarded=rewrite_result,
                    )

                if rewrite_result.blocked:
                    return self._firewall_blocked_result(
                        state, review_run_id, history, attempt, rewrite_result, stage_label="rewritten",
                    )

                raw_rewritten = rewrite_result.output or {}
                version = merge_targeted_rewrite(version, raw_rewritten, closure)
            else:
                allowed_rewrite_sections = sorted(
                    revision_scope_for(self.agent_name, version, decision.blockingIssues)
                )

                rewrite_result = guarded_call(
                    GuardedCallRequest(
                        stage="rewrite",
                        trusted_parts=writer_trusted_parts,
                        untrusted_parts={
                            **writer_untrusted_parts,
                            "candidate_output": _safe_str(version),
                            "reviewer_findings": _safe_str([i.model_dump() for i in decision.blockingIssues]),
                            "allowed_rewrite_sections": _safe_str(allowed_rewrite_sections),
                        },
                        call_fn=lambda v=version, d=decision: _invoke_with_deadline(
                            self.rewrite_agent.rewrite,
                            v,
                            d.blockingIssues,
                            context,
                            agent_name=self.agent_name,
                            deadline=deadline,
                        ),
                        schema=self.config.schema,
                        url_mode=self.config.url_mode,
                        allowed_sources=context.allowed_source_metadata,
                    ),
                    self.firewall,
                )

                if rewrite_result.provider_failed:
                    return self._review_unavailable_result(
                        state, review_run_id, history, attempt, guarded=rewrite_result,
                    )

                if rewrite_result.blocked:
                    return self._firewall_blocked_result(
                        state, review_run_id, history, attempt, rewrite_result, stage_label="rewritten",
                    )

                raw_rewritten = rewrite_result.output or {}
                version, _rewrite_scope = apply_scoped_rewrite(
                    self.agent_name,
                    version,
                    raw_rewritten,
                    decision.blockingIssues,
                )

            # The provider returned a schema-valid complete object, but the
            # deterministic scoped merge restores untouched sections. Re-run
            # the full schema/cross-reference validation on the merged result
            # before it can become the next candidate.
            scoped_validation = validate_detailed(self.config.schema, version)
            version = scoped_validation.data
            version_schema_ok = scoped_validation.valid
            if version_schema_ok:
                state.last_structurally_valid_candidate = version
            generator_provider, generator_model = rewrite_result.provider, rewrite_result.model
            attempt += 1
            semantic_rewrites += 1
            self._append_candidate_record(
                history,
                attempt_number=attempt,
                operation="semantic_rewrite",
                candidate=version,
                guarded=rewrite_result,
                schema_valid_override=version_schema_ok,
            )

    # ------------------------------------------------------------------
    # Structural repair (schema_invalid path)
    # ------------------------------------------------------------------

    def _attempt_structural_repair(
        self,
        version: Candidate,
        attempt: int,
        structural_repairs: int,
        context: ReviewContext,
        writer_trusted_parts: dict[str, str],
        writer_untrusted_parts: dict[str, str],
        state: _PipelineState,
        review_run_id: str,
        history: list[AttemptRecord],
        deadline: float,
    ) -> tuple[Candidate, bool, int, int, PipelineResult | None]:
        if structural_repairs >= self.config.max_structural_repairs:
            terminal = self._schema_invalid_result(state, review_run_id, history, attempt)
            return version, False, attempt, structural_repairs, terminal

        validation = validate_detailed(self.config.schema, version)

        # SE Documentation-only path: contain the structural-repair payload
        # to only the sections the validation errors actually name, instead
        # of resending the complete ~30,000-token candidate and complete
        # top-level schema on every repair attempt -- see
        # se_documentation_structural_repair_scope.py's module docstring.
        # Every other agent falls through to the unchanged full-object path
        # below (byte-identical to before this change).
        if self.agent_name == _SE_DOCUMENTATION_AGENT_NAME:
            return self._attempt_se_documentation_structural_repair(
                version, validation, attempt, structural_repairs, context,
                writer_trusted_parts, writer_untrusted_parts, state,
                review_run_id, history, deadline,
            )

        fix_result = self._run_full_structural_repair(
            validation, context, writer_trusted_parts, writer_untrusted_parts, deadline,
        )

        next_attempt = attempt + 1
        next_structural_repairs = structural_repairs + 1

        if fix_result.provider_failed or fix_result.blocked:
            terminal = self._schema_invalid_result(
                state, review_run_id, history, next_attempt, guarded=fix_result,
            )
            return version, False, next_attempt, next_structural_repairs, terminal

        history.append(
            AttemptRecord(
                attemptNumber=next_attempt,
                stage="rewrite",
                operation="structural_repair",
                outputHash=output_hash(fix_result.output),
                firewallPassed=True,
                schemaValid=fix_result.schema_valid,
                reviewed=False,
                generatorProvider=fix_result.provider,
                generatorModel=fix_result.model,
                kept=False,
            )
        )

        if fix_result.schema_valid:
            state.last_structurally_valid_candidate = fix_result.output or {}

        return (
            fix_result.output or {},
            fix_result.schema_valid,
            next_attempt,
            next_structural_repairs,
            None,
        )

    def _run_full_structural_repair(
        self,
        validation: Any,
        context: ReviewContext,
        writer_trusted_parts: dict[str, str],
        writer_untrusted_parts: dict[str, str],
        deadline: float,
        *,
        prompt: str | None = None,
    ) -> GuardedResult:
        """
        The original, unmodified full-candidate/full-schema structural
        repair call -- factored out unchanged so the SE Documentation path
        can reuse it verbatim for its own payload-gated full-repair
        fallback (see _attempt_se_documentation_structural_repair), instead
        of duplicating this guarded_call block.

        `prompt`, when supplied, is the exact prompt string
        resolve_structural_repair_plan already gated with estimate_tokens()
        -- forwarded to fix_structure() so it is reused verbatim instead of
        rebuilt (see fix_structure's docstring). Every generic-agent caller
        omits it, leaving this call byte-identical to before this parameter
        existed. `_accepts_keyword` keeps older/minimal fix_structure test
        doubles that predate the `prompt` keyword working unchanged, the
        same way `deadline` is handled below.
        """
        extra_kwargs: dict[str, Any] = {}
        if prompt is not None and _accepts_keyword(self.rewrite_agent.fix_structure, "prompt"):
            extra_kwargs["prompt"] = prompt

        return guarded_call(
            GuardedCallRequest(
                stage="rewrite",
                trusted_parts=writer_trusted_parts,
                untrusted_parts={
                    **writer_untrusted_parts,
                    "invalid_candidate": _safe_str(validation.data),
                    "schema_validation_errors": _safe_str(validation.errors),
                },
                call_fn=lambda v=validation.data, e=validation.errors, s=validation.expected_schema, kw=extra_kwargs: (
                    _invoke_with_deadline(
                        self.rewrite_agent.fix_structure,
                        v,
                        agent_name=self.agent_name,
                        validation_errors=e,
                        expected_schema=s,
                        deadline=deadline,
                        **kw,
                    )
                ),
                schema=self.config.schema,
                url_mode=self.config.url_mode,
                allowed_sources=context.allowed_source_metadata,
            ),
            self.firewall,
        )

    def _attempt_se_documentation_structural_repair(
        self,
        version: Candidate,
        validation: Any,
        attempt: int,
        structural_repairs: int,
        context: ReviewContext,
        writer_trusted_parts: dict[str, str],
        writer_untrusted_parts: dict[str, str],
        state: _PipelineState,
        review_run_id: str,
        history: list[AttemptRecord],
        deadline: float,
    ) -> tuple[Candidate, bool, int, int, PipelineResult | None]:
        next_attempt = attempt + 1
        next_structural_repairs = structural_repairs + 1

        try:
            plan = resolve_structural_repair_plan(
                validation.data, validation.errors, validation.expected_schema,
                agent_name=self.agent_name,
                schema_cls=self.config.schema,
            )
        except StructuralRepairPayloadTooLargeError as exc:
            # Neither a scoped repair nor a payload-safe full repair is
            # possible -- fail honestly (existing schema_invalid terminal,
            # preserving whatever prior valid candidate the pipeline
            # already tracks) rather than send a known-oversized request to
            # any provider, or truncate the payload.
            logger.warning("se_documentation.structural_repair_payload_too_large reason=%s", exc)
            terminal = self._schema_invalid_result(state, review_run_id, history, next_attempt)
            return version, False, next_attempt, next_structural_repairs, terminal

        if plan.use_full_repair:
            logger.info(
                "se_documentation.structural_repair_mode mode=full_payload_within_limit "
                "estimated_prompt_tokens=%d",
                estimate_tokens(plan.prompt),
            )
            fix_result = self._run_full_structural_repair(
                validation, context, writer_trusted_parts, writer_untrusted_parts, deadline,
                prompt=plan.prompt,
            )

            if fix_result.provider_failed or fix_result.blocked:
                terminal = self._schema_invalid_result(
                    state, review_run_id, history, next_attempt, guarded=fix_result,
                )
                return version, False, next_attempt, next_structural_repairs, terminal

            # The generic full-repair path (_run_full_structural_repair)
            # accepts fix_structure()'s raw response as the complete
            # document unchanged -- correct for every other agent, but SE
            # Documentation must still guarantee _NEVER_LLM_REWRITABLE_
            # FIELDS can never change even on this fallback path (the
            # scoped path's merge_structural_repair already guarantees this;
            # see restore_never_llm_rewritable_fields's docstring). The
            # restored candidate is the one actually validated/returned, so
            # an immutable-field validation error that the platform's own
            # deterministic logic can't fix fails safely as schema_invalid
            # instead of accepting an LLM-guessed replacement.
            restored_output = restore_never_llm_rewritable_fields(validation.data, fix_result.output or {})
            restored_validation = validate_detailed(self.config.schema, restored_output)

            history.append(
                AttemptRecord(
                    attemptNumber=next_attempt,
                    stage="rewrite",
                    operation="structural_repair",
                    outputHash=output_hash(restored_validation.data),
                    firewallPassed=True,
                    schemaValid=restored_validation.valid,
                    reviewed=False,
                    generatorProvider=fix_result.provider,
                    generatorModel=fix_result.model,
                    kept=False,
                )
            )

            if restored_validation.valid:
                state.last_structurally_valid_candidate = restored_validation.data

            return (
                restored_validation.data,
                restored_validation.valid,
                next_attempt,
                next_structural_repairs,
                None,
            )

        closure = plan.closure
        assert closure is not None  # resolve_structural_repair_plan's contract

        logger.info(
            "se_documentation.structural_repair_mode mode=scoped sections=%d "
            "estimated_prompt_tokens=%d",
            len(closure.allowed_sections), estimate_tokens(plan.prompt),
        )

        scoped_kwargs: dict[str, Any] = {}
        if _accepts_keyword(self.rewrite_agent.fix_structure_scoped, "prompt"):
            scoped_kwargs["prompt"] = plan.prompt

        fix_result = guarded_call(
            GuardedCallRequest(
                stage="rewrite",
                trusted_parts=writer_trusted_parts,
                untrusted_parts={
                    **writer_untrusted_parts,
                    "invalid_candidate_sections": _safe_str(
                        build_compact_rewrite_candidate(validation.data, closure)
                    ),
                    "schema_validation_errors": _safe_str(validation.errors),
                    "repaired_sections": _safe_str(sorted(closure.allowed_sections)),
                },
                # No `schema=` here on purpose: the raw response is a
                # PARTIAL object (only the requested sections), which would
                # fail full-candidate-schema validation even when perfectly
                # correct. The merged, complete candidate is validated
                # below via the same validate_detailed the full-repair path
                # uses.
                call_fn=lambda v=validation.data, c=closure, e=validation.errors, kw=scoped_kwargs: _invoke_with_deadline(
                    self.rewrite_agent.fix_structure_scoped,
                    v,
                    c,
                    agent_name=self.agent_name,
                    validation_errors=e,
                    schema_cls=self.config.schema,
                    deadline=deadline,
                    **kw,
                ),
                url_mode=self.config.url_mode,
                allowed_sources=context.allowed_source_metadata,
            ),
            self.firewall,
        )

        if fix_result.provider_failed or fix_result.blocked:
            terminal = self._schema_invalid_result(
                state, review_run_id, history, next_attempt, guarded=fix_result,
            )
            return version, False, next_attempt, next_structural_repairs, terminal

        try:
            merged = merge_structural_repair(validation.data, fix_result.output, closure)
        except StructuralRepairScopeError as exc:
            # Missing/unsolicited/malformed scoped response -- never merged,
            # never accepted as a partial repair.
            logger.warning("se_documentation.structural_repair_response_rejected reason=%s", exc)
            terminal = self._schema_invalid_result(
                state, review_run_id, history, next_attempt, guarded=fix_result,
            )
            return version, False, next_attempt, next_structural_repairs, terminal

        # Full-document validation remains authoritative: a scoped repair is
        # only ever successful when the COMPLETE merged candidate passes the
        # complete schema -- never because the repaired fragment alone
        # validated.
        merged_validation = validate_detailed(self.config.schema, merged)
        merged = merged_validation.data
        schema_valid = merged_validation.valid

        logger.info(
            "se_documentation.structural_repair_result schema_valid=%s",
            schema_valid,
        )

        history.append(
            AttemptRecord(
                attemptNumber=next_attempt,
                stage="rewrite",
                operation="structural_repair",
                outputHash=output_hash(merged),
                firewallPassed=True,
                schemaValid=schema_valid,
                reviewed=False,
                generatorProvider=fix_result.provider,
                generatorModel=fix_result.model,
                kept=False,
            )
        )

        if schema_valid:
            state.last_structurally_valid_candidate = merged

        return (merged, schema_valid, next_attempt, next_structural_repairs, None)

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------

    def _append_candidate_record(
        self,
        history: list[AttemptRecord],
        *,
        attempt_number: int,
        operation: str,
        candidate: Candidate,
        guarded: GuardedResult,
        schema_valid_override: bool | None = None,
    ) -> AttemptRecord:
        record = AttemptRecord(
            attemptNumber=attempt_number,
            stage="writer" if operation == "writer" else "rewrite",
            operation=operation,  # type: ignore[arg-type]
            outcome="candidate_produced",
            outputHash=output_hash(candidate),
            firewallPassed=not guarded.blocked,
            firewallFlags=[
                finding.rule
                for finding in self._output_findings_of(guarded)
            ],
            schemaValid=(
                guarded.schema_valid
                if schema_valid_override is None
                else schema_valid_override
            ),
            reviewed=False,
            generatorProvider=guarded.provider,
            generatorModel=guarded.model,
            kept=False,
        )
        history.append(record)
        return record

    @staticmethod
    def _latest_record_for_candidate(
        history: list[AttemptRecord],
        candidate: Candidate,
    ) -> AttemptRecord | None:
        candidate_hash = output_hash(candidate)
        for record in reversed(history):
            if record.outputHash == candidate_hash:
                return record
        return None

    def _mark_candidate_reviewed(
        self,
        history: list[AttemptRecord],
        *,
        candidate: Candidate,
        findings: ReviewerFindings,
        decision: RewriteDecision,
        reviewer_result: GuardedResult,
        fallback_attempt_number: int,
        fallback_generator_provider: str | None,
        fallback_generator_model: str | None,
    ) -> AttemptRecord:
        record = self._latest_record_for_candidate(history, candidate)

        # Defensive fallback for integrations that construct pipeline state or
        # history manually. Production paths should already have recorded the
        # Writer/Rewrite candidate before semantic review starts.
        if record is None:
            record = AttemptRecord(
                attemptNumber=fallback_attempt_number,
                stage="rewrite" if fallback_attempt_number else "writer",
                operation="semantic_rewrite" if fallback_attempt_number else "writer",
                outcome="candidate_produced",
                outputHash=output_hash(candidate),
                firewallPassed=True,
                schemaValid=True,
                reviewed=False,
                generatorProvider=fallback_generator_provider,
                generatorModel=fallback_generator_model,
                kept=False,
            )
            history.append(record)

        record.reviewed = True
        record.reviewerFindings = findings
        record.decision = decision
        record.reviewerProvider = reviewer_result.provider
        record.reviewerModel = reviewer_result.model
        return record

    @staticmethod
    def _output_origin(output: Candidate, history: list[AttemptRecord]) -> str:
        if not output:
            return "none"

        output_digest = output_hash(output)
        for record in reversed(history):
            if record.outputHash == output_digest:
                return record.operation
        return "unknown"

    @staticmethod
    def _output_review_level(
        *,
        usable: bool,
        reviewer_findings: ReviewerFindings | None,
    ) -> str:
        if not usable:
            return "none"
        if reviewer_findings is None:
            return "structural_only"
        return "approved" if not reviewer_findings.issues else "reviewed_with_warnings"

    # ------------------------------------------------------------------
    # Terminal-result helpers -- each implements the candidate-selection
    # table from the approved design: a version is only ever shown if it is
    # last_approved_output or last_reviewed_noncritical_candidate (or, only
    # when the agent's registry entry opts in, last_structurally_valid_candidate).
    # ------------------------------------------------------------------

    def _review_unavailable_result(
        self, state: _PipelineState, review_run_id: str, history: list[AttemptRecord], attempt: int,
        *, guarded: GuardedResult | None = None,
    ) -> PipelineResult:
        input_findings = self._input_findings_of(guarded)
        output_findings = self._output_findings_of(guarded)

        if state.last_approved_output:
            output, findings = state.last_approved_output
            return self._result(
                "review_unavailable", usable=True, output=output, reviewer_findings=findings,
                warning="A newer answer could not be verified; showing your previously approved answer.",
                review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
                firewall_input_findings=input_findings, firewall_output_findings=output_findings,
            )

        if state.last_reviewed_noncritical_candidate:
            output, findings = state.last_reviewed_noncritical_candidate
            return self._result(
                "review_unavailable", usable=True, output=output, reviewer_findings=findings,
                warning="A newer answer could not be verified; showing the last verified answer.",
                review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
                firewall_input_findings=input_findings, firewall_output_findings=output_findings,
            )

        if self.config.allow_unreviewed_output and state.last_structurally_valid_candidate:
            return self._result(
                "review_unavailable", usable=True, output=state.last_structurally_valid_candidate,
                warning="This answer passed structural checks but semantic review could not be completed.",
                review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
                firewall_input_findings=input_findings, firewall_output_findings=output_findings,
            )

        return self._result(
            "review_unavailable", usable=False, output={},
            warning="Semantic review could not be completed and no safe answer is available.",
            review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
            firewall_input_findings=input_findings, firewall_output_findings=output_findings,
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

        # TEMPORARY diagnostic bypass (SE_DOCUMENTATION_CRITICAL_GATE_DISABLED=1,
        # SEDocumentationAgent only) -- shows the last structurally-valid AI
        # candidate even though a critical issue was never resolved, instead
        # of the generic deterministic fallback. The critical finding is
        # still attached via reviewer_findings so it stays visible in the UI
        # -- this is NOT the same as fixing the issue, only surfacing raw AI
        # content for inspection. Unset before treating output as reviewed.
        if (
            self.agent_name == "SEDocumentationAgent"
            and os.getenv("SE_DOCUMENTATION_CRITICAL_GATE_DISABLED", "").strip().lower() in ("1", "true", "yes")
            and state.last_structurally_valid_candidate
        ):
            logger.warning(
                "review_pipeline.critical_gate_DISABLED_bypassing_rejection agent=%s review_run_id=%s",
                self.agent_name, review_run_id,
            )
            return self._result(
                "rejected", usable=True, output=state.last_structurally_valid_candidate,
                reviewer_findings=findings, decision=decision,
                warning="[CRITICAL GATE DISABLED FOR DEBUGGING] A critical issue was never resolved -- "
                        "verify manually before using this document.",
                review_run_id=review_run_id, history=history, attempts=attempt + 1,
            )

        return self._result(
            "rejected", usable=False, output={}, reviewer_findings=findings, decision=decision,
            warning="A critical issue remained after the maximum number of rewrites and no safe earlier version exists.",
            review_run_id=review_run_id, history=history, attempts=attempt + 1,
        )

    def _schema_invalid_result(
        self, state: _PipelineState, review_run_id: str, history: list[AttemptRecord], attempt: int,
        *, guarded: GuardedResult | None = None,
    ) -> PipelineResult:
        input_findings = self._input_findings_of(guarded)
        output_findings = self._output_findings_of(guarded)

        if state.last_reviewed_noncritical_candidate:
            output, findings = state.last_reviewed_noncritical_candidate
            return self._result(
                "schema_invalid", usable=True, output=output, reviewer_findings=findings,
                warning="A later version never became structurally valid; showing the last verified answer.",
                review_run_id=review_run_id, history=history, attempts=attempt,
                firewall_input_findings=input_findings, firewall_output_findings=output_findings,
            )

        return self._result(
            "schema_invalid", usable=False, output={},
            warning="Output never became structurally valid.",
            review_run_id=review_run_id, history=history, attempts=attempt,
            firewall_input_findings=input_findings, firewall_output_findings=output_findings,
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
        input_findings = self._input_findings_of(guarded)
        output_findings = self._output_findings_of(guarded)

        if state.last_reviewed_noncritical_candidate:
            output, findings = state.last_reviewed_noncritical_candidate
            return self._result(
                "firewall_blocked", usable=True, output=output, reviewer_findings=findings,
                warning=f"The {stage_label} answer was blocked by the content firewall; showing the last verified answer.",
                review_run_id=review_run_id, history=history, attempts=attempt + 1,
                firewall_input_findings=input_findings, firewall_output_findings=output_findings,
            )

        return self._result(
            "firewall_blocked", usable=False, output={},
            warning=f"The {stage_label} answer was blocked by the content firewall and no safe earlier version exists.",
            review_run_id=review_run_id, history=history, attempts=attempt + 1,
            firewall_input_findings=input_findings, firewall_output_findings=output_findings,
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

        # Same fallback _review_unavailable_result already uses when the
        # Reviewer call itself fails/errors -- applied here too for
        # consistency, so a request that ran out of wall-clock budget
        # (e.g. a slow Reviewer call on a tight end-to-end deadline) isn't
        # treated differently from one where the Reviewer call errored
        # outright. allow_unreviewed_output defaults to False, so this is a
        # no-op for every agent that hasn't explicitly opted in.
        if self.config.allow_unreviewed_output and state.last_structurally_valid_candidate:
            return self._result(
                "review_unavailable", usable=True, output=state.last_structurally_valid_candidate,
                warning="This answer passed structural checks but semantic review could not be "
                        "completed before the request's time budget ran out.",
                review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
            )

        return self._result(
            "review_unavailable", usable=False, output={},
            warning="The review process exceeded its time budget before completing a single review.",
            review_run_id=review_run_id, history=history, attempts=attempt, review_unavailable=True,
        )

    # ------------------------------------------------------------------
    # Small utilities
    # ------------------------------------------------------------------

    def _time_budget_exceeded(self, deadline: float) -> bool:
        # Compares against the single deadline value threaded through this
        # entire run (see run()'s docstring) -- deliberately NOT
        # self.config.max_total_seconds re-added to a fresh "now", which
        # would silently re-derive a different (later) cutoff than the one
        # actually passed to the Writer/Reviewer/Rewrite stages.
        return time.monotonic() > deadline

    @staticmethod
    def _input_findings_of(guarded: GuardedResult | None) -> list:
        verdict = getattr(guarded, "input_verdict", None) if guarded else None
        return verdict.findings if verdict else []

    @staticmethod
    def _output_findings_of(guarded: GuardedResult | None) -> list:
        verdict = getattr(guarded, "output_verdict", None) if guarded else None
        return verdict.findings if verdict else []

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
        firewall_input_findings: list | None = None,
        firewall_output_findings: list | None = None,
    ) -> PipelineResult:
        if usable and output:
            selected_record = self._latest_record_for_candidate(history, output)
            if selected_record is not None:
                selected_record.kept = True

        return PipelineResult(
            status=status,  # type: ignore[arg-type]
            usable=usable,
            output=output or {},
            displayable=usable and bool(output),
            outputOrigin=self._output_origin(output or {}, history),  # type: ignore[arg-type]
            outputReviewLevel=self._output_review_level(
                usable=usable,
                reviewer_findings=reviewer_findings,
            ),  # type: ignore[arg-type]
            reviewUnavailable=review_unavailable,
            warning=warning,
            reviewerFindings=reviewer_findings,
            decision=decision,
            attempts=attempts,
            attemptHistory=history,
            reviewRunId=review_run_id,
            firewallInputFindings=firewall_input_findings or [],
            firewallOutputFindings=firewall_output_findings or [],
        )