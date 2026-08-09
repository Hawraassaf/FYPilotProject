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
from app.review.models import AttemptRecord, PipelineResult, ReviewerFindings, ReviewerIssue, RewriteDecision
from app.review.registry import AgentReviewConfig, get_agent_config
from app.review.review_decision_engine import ReviewDecisionEngine
from app.review.reviewer_agent import ReviewerAgent
from app.review.rewrite_agent import RewriteAgent
from app.review.schema_validation import validate_detailed
from app.review.se_documentation_rewrite_scope import (
    RewriteClosure,
    ScopeResolutionError,
    _collect_seed_ids,
    build_compact_rewrite_candidate,
    group_blocking_issues_by_primary_sections,
    merge_targeted_rewrite,
    resolve_rewrite_closure,
)
from app.review.se_documentation_structural_repair_scope import (
    StructuralRepairPayloadTooLargeError,
    StructuralRepairScopeError,
    estimate_tokens,
    merge_structural_repair,
    resolve_structural_repair_plan,
    resolve_structural_repair_plans,
    restore_never_llm_rewritable_fields,
)
from app.review.section_scope import apply_scoped_rewrite, revision_scope_for
from app.review.se_documentation_id_lineage import added_ids, compute_section_lineage, diff_sections_against_baseline
from app.review.se_documentation_structural_invariants import (
    RECONCILABLE_KINDS,
    diagnose_structural_invariants,
    reconcile_deterministically,
    rebuild_mermaid_activity_diagram,
    rebuild_mermaid_class_diagram,
    rebuild_mermaid_erd,
    rebuild_mermaid_sequence_diagram,
    rebuild_traceability_matrix,
    resolve_minimal_affected_sections,
)
from app.review.se_documentation_deterministic_normalization import (
    normalize_database_entities_dicts,
    rebuild_assumptions_disclosure,
)
from app.review.se_documentation_debug_snapshot import maybe_write_se_documentation_debug_snapshot
from app.services.json_reliability import OUTPUT_TRUNCATED, classify_truncation_failure
from app.services.llm_provider import LLMResult, ProviderChain

# Minimum remaining absolute-deadline seconds required before ATTEMPTING one
# more per-section repair/rewrite LLM call (structural repair loop and SE
# Documentation's per-primary-section semantic rewrite loop below). Matches
# ProviderChain._MIN_SECONDS_PER_PROVIDER_ATTEMPT (llm_provider.py) and
# SEDocumentationOrchestratorAgent._MIN_SECONDS_PER_SECTION_ATTEMPT
# (se_documentation_orchestrator.py) -- same reasoning: below this many
# seconds, starting one more provider call has too little chance of
# completing before the caller's own absolute deadline, so the remaining
# sections in the queue are left unrepaired (schema/merge validation then
# fails or succeeds honestly on whatever was actually repaired) rather than
# starting a call that will almost certainly be cut off by the deadline
# itself instead of by the provider's own max_tokens.
_MIN_SECONDS_PER_SECTION_REPAIR_ATTEMPT = 4.0

# Sections whose row-identity field this pipeline can diff against the last
# known-good candidate to detect a retroactive "added this pass" id/name --
# see _attempt_se_documentation_invariant_reconciliation. databaseEntities is
# handled separately (by `name`, not `entityId`/row-identity) because its
# duplicate check (DUPLICATE_ENTITY_NAME) is itself name-based -- see
# se_documentation_structural_invariants.py.
_INVARIANT_DUPLICATE_CHECK_SECTIONS = (
    "functionalRequirements", "nonFunctionalRequirements", "useCases", "edgeCases",
    "systemModules", "testingPlan", "uiScreens", "apiIntegrationPoints",
)
_INVARIANT_REQUIREMENT_SECTIONS = ("functionalRequirements", "nonFunctionalRequirements")

logger = logging.getLogger("fypilot-review-pipeline")

Candidate = dict[str, Any]
ReviewedCandidate = tuple[Candidate, ReviewerFindings]

_SE_DOCUMENTATION_AGENT_NAME = "SEDocumentationAgent"


def _is_truncated_failure(guarded: GuardedResult) -> bool:
    """
    True when a provider_failed GuardedResult's underlying LLMResult was cut
    off at the provider's own output-token ceiling (stop_reason/finish_reason
    in {"length", "max_tokens", "max_output_tokens"} -- see
    json_reliability.looks_truncated), as opposed to any other malformed-JSON
    or transport cause. See json_reliability.classify_truncation_failure for
    the underlying classification; this wrapper just turns it into the
    boolean callers actually branch on.
    """
    return classify_truncation_failure(guarded.parse_diagnostics) == OUTPUT_TRUNCATED


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
                # SE Documentation-only path: one bounded rewrite call per
                # PRIMARY section (or tightly-coupled group of sections a
                # single reviewer finding names together), never one call
                # spanning every blocking issue -- see
                # se_documentation_rewrite_scope.py's module docstring for
                # the ~30,000-token full-document rewrite request this
                # replaces, and group_blocking_issues_by_primary_sections's
                # docstring for the per-group split. Every other agent falls
                # through to the unchanged full-object path below.
                try:
                    issue_groups = group_blocking_issues_by_primary_sections(
                        version, decision.blockingIssues,
                    )
                except ScopeResolutionError as exc:
                    logger.warning("se_documentation.rewrite_scope_resolution_failed reason=%s", exc)
                    return self._review_unavailable_result(
                        state, review_run_id, history, attempt,
                    )

                merged_version = version
                rewrite_result: GuardedResult | None = None
                last_successful_rewrite_result: GuardedResult | None = None

                for group_roots, group_issues in issue_groups:
                    remaining = deadline - time.monotonic()
                    if remaining < _MIN_SECONDS_PER_SECTION_REPAIR_ATTEMPT:
                        logger.info(
                            "se_documentation.semantic_rewrite_group_skipped_insufficient_time "
                            "roots=%s remaining=%.1fs",
                            sorted(group_roots), remaining,
                        )
                        break

                    try:
                        group_closure = resolve_rewrite_closure(merged_version, group_issues)
                    except ScopeResolutionError as exc:
                        logger.warning(
                            "se_documentation.rewrite_scope_resolution_failed roots=%s reason=%s",
                            sorted(group_roots), exc,
                        )
                        return self._review_unavailable_result(
                            state, review_run_id, history, attempt,
                        )

                    rewrite_result = self._attempt_one_semantic_rewrite_group(
                        merged_version, group_closure, group_issues, context,
                        writer_trusted_parts, writer_untrusted_parts, deadline,
                    )

                    if rewrite_result.blocked:
                        return self._firewall_blocked_result(
                            state, review_run_id, history, attempt, rewrite_result, stage_label="rewritten",
                        )

                    if rewrite_result.provider_failed:
                        logger.warning(
                            "se_documentation.semantic_rewrite_group_failed roots=%s truncated=%s",
                            sorted(group_roots), _is_truncated_failure(rewrite_result),
                        )
                        continue

                    raw_rewritten = rewrite_result.output or {}
                    if "databaseEntities" in group_closure.allowed_sections:
                        # Restore any entity this group's rewrite removed or
                        # renamed WITHOUT the reviewer finding actually
                        # requesting it (see se_documentation_id_lineage's
                        # compute_section_lineage) -- "requested" here means
                        # an entity id/name the finding's own text names,
                        # same matching se_documentation_rewrite_scope.py's
                        # own seed-id resolution already uses. Then apply
                        # the SAME Writer-time hard-rule normalization a
                        # freshly-generated section already gets.
                        requested_entity_ids = _collect_seed_ids(merged_version, "databaseEntities", group_issues)
                        lineage_result = compute_section_lineage(
                            "databaseEntities",
                            merged_version.get("databaseEntities") or [],
                            raw_rewritten.get("databaseEntities") or [],
                            requested_ids=frozenset(requested_entity_ids),
                        )
                        raw_rewritten = dict(raw_rewritten)
                        raw_rewritten["databaseEntities"] = normalize_database_entities_dicts(
                            lineage_result.items
                        )

                    merged_version = merge_targeted_rewrite(merged_version, raw_rewritten, group_closure)
                    last_successful_rewrite_result = rewrite_result

                if last_successful_rewrite_result is None:
                    return self._review_unavailable_result(
                        state, review_run_id, history, attempt, guarded=rewrite_result,
                    )

                version = merged_version
                rewrite_result = last_successful_rewrite_result

                # Same generated-derivative rebuild the structural-repair
                # path applies (see _attempt_se_documentation_structural_
                # repair's matching comment) -- a semantic rewrite that
                # changes databaseEntities/requirements/etc. must not leave
                # traceabilityMatrix/mermaidERD/mermaidClassDiagram or the
                # assumptions disclosure stale.
                version["assumptions"] = rebuild_assumptions_disclosure(version)
                version["traceabilityMatrix"] = rebuild_traceability_matrix(version)
                version["mermaidERD"] = rebuild_mermaid_erd(version)
                version["mermaidClassDiagram"] = rebuild_mermaid_class_diagram(version)
                version["activityDiagram"] = rebuild_mermaid_activity_diagram(version)
                version["sequenceDiagram"] = rebuild_mermaid_sequence_diagram(version)
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

        if self.agent_name == _SE_DOCUMENTATION_AGENT_NAME:
            # Opt-in, disabled-by-default (see se_documentation_debug_
            # snapshot.py) -- a no-op unless SE_DOCUMENTATION_DEBUG_SNAPSHOT_
            # DIR is explicitly set. Written immediately before the whole-
            # document structural validation below, using this run's own
            # review_run_id, so a failed run's candidate can be replayed
            # locally afterward with zero provider calls.
            maybe_write_se_documentation_debug_snapshot(version, request_id=review_run_id)

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
            plans = resolve_structural_repair_plans(
                validation.data, validation.errors, validation.expected_schema,
                agent_name=self.agent_name,
                schema_cls=self.config.schema,
            )
        except StructuralRepairPayloadTooLargeError as exc:
            # An unlocalizable ("$") validation error -- e.g. SEDocumentation
            # CandidateSchema._check_structural_invariants' cross-section
            # id/reference checks -- with a full-document repair prompt too
            # large to safely send. Before failing outright, try to
            # reconcile the SPECIFIC known invariant violation(s): first
            # deterministically (no LLM call), then with at most one
            # targeted correction scoped to only the sections involved --
            # never by resending the complete document (see
            # _attempt_se_documentation_invariant_reconciliation's
            # docstring). Only ever attempted for genuinely unlocalizable
            # structural errors, i.e. exactly this branch.
            logger.warning("se_documentation.structural_repair_payload_too_large reason=%s", exc)
            logger.warning(
                "se_documentation.unresolvable_validation_errors errors=%s",
                _safe_str(validation.errors, max_length=4000),
            )

            reconciled, reconciled_ok, reconciler_provider, reconciler_model = (
                self._attempt_se_documentation_invariant_reconciliation(
                    validation.data, state, context, writer_trusted_parts, writer_untrusted_parts, deadline,
                )
            )

            history.append(
                AttemptRecord(
                    attemptNumber=next_attempt,
                    stage="rewrite",
                    operation="structural_repair",
                    outputHash=output_hash(reconciled),
                    firewallPassed=True,
                    schemaValid=reconciled_ok,
                    reviewed=False,
                    generatorProvider=reconciler_provider,
                    generatorModel=reconciler_model,
                    kept=False,
                )
            )

            if reconciled_ok:
                state.last_structurally_valid_candidate = reconciled
                return reconciled, True, next_attempt, next_structural_repairs, None

            terminal = self._schema_invalid_result(state, review_run_id, history, next_attempt)
            return version, False, next_attempt, next_structural_repairs, terminal

        if len(plans) == 1 and plans[0].use_full_repair:
            plan = plans[0]
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

        # Per-section repair queue: one bounded LLM call per plan (each
        # plan's closure names exactly one top-level section -- see
        # resolve_structural_repair_plans), never a single call spanning
        # multiple sections. `plans` is already in deterministic (sorted
        # section name) order. A section whose repair fails (provider
        # failure, firewall block, or a rejected unsolicited/missing
        # response) is simply left unrepaired -- its original content is
        # never touched -- and the loop proceeds to the remaining sections;
        # the final full-document validate_detailed call below is what
        # honestly decides whether the overall repair succeeded, never any
        # individual section's own result.
        merged = dict(validation.data)
        for plan in plans:
            closure = plan.closure
            assert closure is not None  # per-section plans always carry a closure
            section_name = next(iter(closure.allowed_sections))

            remaining = deadline - time.monotonic()
            if remaining < _MIN_SECONDS_PER_SECTION_REPAIR_ATTEMPT:
                logger.info(
                    "se_documentation.structural_repair_section_skipped_insufficient_time "
                    "section=%s remaining=%.1fs",
                    section_name, remaining,
                )
                break

            logger.info(
                "se_documentation.structural_repair_mode mode=scoped section=%s "
                "estimated_prompt_tokens=%d",
                section_name, estimate_tokens(plan.prompt),
            )

            fix_result = self._attempt_one_structural_repair_section(
                merged, closure, plan.prompt, validation.errors,
                context, writer_trusted_parts, writer_untrusted_parts, deadline,
            )

            if fix_result.provider_failed or fix_result.blocked:
                logger.warning(
                    "se_documentation.structural_repair_section_failed section=%s "
                    "truncated=%s blocked=%s",
                    section_name, _is_truncated_failure(fix_result), fix_result.blocked,
                )
                history.append(
                    AttemptRecord(
                        attemptNumber=next_attempt,
                        stage="rewrite",
                        operation="structural_repair",
                        outputHash=output_hash(merged),
                        firewallPassed=not fix_result.blocked,
                        schemaValid=False,
                        reviewed=False,
                        generatorProvider=fix_result.provider,
                        generatorModel=fix_result.model,
                        kept=False,
                    )
                )
                continue

            try:
                merged = merge_structural_repair(merged, fix_result.output, closure)
                if section_name == "databaseEntities":
                    # Reuses SEDocumentationOrchestratorAgent's own Writer-
                    # time hard-rule normalization (see
                    # se_documentation_deterministic_normalization.py) so a
                    # structural repair to databaseEntities gets the SAME
                    # guarantees a freshly-generated section already has
                    # (no empty/too-few fields, exactly one primary key,
                    # PasswordHash not Password, no fabricated foreign keys)
                    # instead of leaving those hard rules Writer-only.
                    merged["databaseEntities"] = normalize_database_entities_dicts(
                        merged.get("databaseEntities") or []
                    )
            except StructuralRepairScopeError as exc:
                # Missing/unsolicited/malformed scoped response for THIS
                # section -- never merged, never accepted as a partial
                # repair. Previously merged sections are untouched.
                logger.warning(
                    "se_documentation.structural_repair_response_rejected section=%s reason=%s",
                    section_name, exc,
                )
                history.append(
                    AttemptRecord(
                        attemptNumber=next_attempt,
                        stage="rewrite",
                        operation="structural_repair",
                        outputHash=output_hash(merged),
                        firewallPassed=True,
                        schemaValid=False,
                        reviewed=False,
                        generatorProvider=fix_result.provider,
                        generatorModel=fix_result.model,
                        kept=False,
                    )
                )
                continue

            history.append(
                AttemptRecord(
                    attemptNumber=next_attempt,
                    stage="rewrite",
                    operation="structural_repair",
                    outputHash=output_hash(merged),
                    firewallPassed=True,
                    schemaValid=True,
                    reviewed=False,
                    generatorProvider=fix_result.provider,
                    generatorModel=fix_result.model,
                    kept=False,
                )
            )

        # Rebuild every generated-derivative field from the (now normalized)
        # merged sections before final validation -- traceabilityMatrix/
        # mermaidERD/mermaidClassDiagram are never LLM targets (see
        # _NEVER_LLM_REWRITABLE_FIELDS) and merge_structural_repair always
        # restores them from the PRE-repair candidate, so without this they
        # would silently go stale the moment a repair actually changes a
        # section they're derived from (observed live: a databaseEntities
        # repair leaving mermaidERD frozen at its old, now-inconsistent
        # state). Cheap and idempotent -- rebuilding every pass, whether or
        # not this pass actually touched a relevant section, is simpler and
        # no less correct than tracking exactly which sections changed.
        merged["assumptions"] = rebuild_assumptions_disclosure(merged)
        merged["traceabilityMatrix"] = rebuild_traceability_matrix(merged)
        merged["mermaidERD"] = rebuild_mermaid_erd(merged)
        merged["mermaidClassDiagram"] = rebuild_mermaid_class_diagram(merged)
        merged["activityDiagram"] = rebuild_mermaid_activity_diagram(merged)
        merged["sequenceDiagram"] = rebuild_mermaid_sequence_diagram(merged)

        # Full-document validation remains authoritative: the repair queue
        # is only ever successful when the COMPLETE merged candidate (every
        # section that was repaired, plus every section left untouched)
        # passes the complete schema -- never because any individual
        # section's own repair response validated in isolation.
        merged_validation = validate_detailed(self.config.schema, merged)
        merged = merged_validation.data
        schema_valid = merged_validation.valid

        logger.info(
            "se_documentation.structural_repair_result schema_valid=%s sections=%d",
            schema_valid, len(plans),
        )

        if schema_valid:
            state.last_structurally_valid_candidate = merged

        return (merged, schema_valid, next_attempt, next_structural_repairs, None)

    def _attempt_se_documentation_invariant_reconciliation(
        self,
        candidate: Candidate,
        state: _PipelineState,
        context: ReviewContext,
        writer_trusted_parts: dict[str, str],
        writer_untrusted_parts: dict[str, str],
        deadline: float,
    ) -> tuple[Candidate, bool, str | None, str | None]:
        """
        Returns (candidate, resolved, provider, model) -- `provider`/`model`
        are None whenever the resolution was entirely deterministic (no LLM
        call was made), and reflect the actual targeted-correction call's own
        provider/model whenever that path was used -- so callers never
        attribute a fix to a provider that never ran (see this task's
        provenance-honesty requirement).

        Reconciles a whole-document structural-invariant failure (an
        unlocalizable "$" validation error from SEDocumentationCandidateSchema
        ._check_structural_invariants -- see
        se_documentation_structural_invariants.py's module docstring for why
        the per-section repair queue cannot localize this) WITHOUT ever
        resending the complete document for a full LLM repair.

        Order: diagnose structured issues -> deterministic reconciliation
        (duplicate ids/entity names resolved via a canonical-id allocator,
        dangling references remapped via renames detected against the last
        known-good candidate) -> if issues remain, exactly ONE bounded
        targeted correction scoped to the minimal section closure those
        issues name -> rebuild traceabilityMatrix/mermaidERD (both are
        always fully derived from the other sections, never an LLM target --
        see rebuild_traceability_matrix/rebuild_mermaid_erd) -> re-validate
        the complete document. Returns (candidate, False) -- never a
        partially-reconciled candidate reported as valid -- whenever even the
        one targeted correction attempt does not produce a fully valid
        document; the caller then falls back to the existing, unchanged
        schema_invalid terminal.
        """
        # Deterministic normalization pass FIRST -- a safety net independent
        # of whichever earlier rewrite call actually touched databaseEntities
        # or introduced inferred/assumed content (that per-rewrite wiring is
        # the FIRST line of defense; this makes the guarantee unconditional
        # by the time this whole-document fallback stage runs). Cheap,
        # idempotent, and reuses the exact same Writer-time hard rules --
        # see se_documentation_deterministic_normalization.py.
        candidate = dict(candidate)
        candidate["databaseEntities"] = normalize_database_entities_dicts(candidate.get("databaseEntities") or [])
        candidate["assumptions"] = rebuild_assumptions_disclosure(candidate)

        # Full diagnostic pass, over every kind diagnose_structural_
        # invariants knows how to detect (not just the reconcilable ones) --
        # logged before any repair decision is made, so a live failure is
        # always explainable from the logs afterward even when this module
        # cannot yet act on it.
        all_issues = diagnose_structural_invariants(candidate)
        if all_issues:
            logger.warning(
                "se_documentation.invariant_diagnostics_detected count=%d kinds=%s details=%s",
                len(all_issues),
                sorted({issue.kind for issue in all_issues}),
                _safe_str([issue.description for issue in all_issues], max_length=4000),
            )

        # Only ever act on the subset this module has a verified safe
        # reconciliation strategy for (see RECONCILABLE_KINDS' docstring) --
        # every other detected issue is now VISIBLE in the log line above,
        # but never triggers a deterministic edit or an LLM call on its own.
        issues = [issue for issue in all_issues if issue.kind in RECONCILABLE_KINDS]

        if not issues:
            if all_issues:
                # Something WAS detected (visible in the log line above) but
                # none of it is a kind reconcile_deterministically/targeted-
                # correction acts on -- this does NOT necessarily mean
                # failure: the normalization pre-pass above may already have
                # fixed everything (e.g. every remaining `all_issues` entry
                # was itself normalization-only, like PLAINTEXT_PASSWORD_
                # FIELD, and normalize_database_entities_dicts already fixed
                # the underlying data even though diagnose_structural_
                # invariants -- run BEFORE re-checking -- still listed the
                # pre-normalization issue.) Only full-document validation
                # below can decide honestly.
                logger.warning(
                    "se_documentation.invariant_reconciliation_visibility_only_no_action_taken "
                    "kinds=%s", sorted({issue.kind for issue in all_issues}),
                )
            else:
                logger.info("se_documentation.invariant_reconciliation_resolved_by_normalization_alone")

            candidate["traceabilityMatrix"] = rebuild_traceability_matrix(candidate)
            candidate["mermaidERD"] = rebuild_mermaid_erd(candidate)
            candidate["mermaidClassDiagram"] = rebuild_mermaid_class_diagram(candidate)
            candidate["activityDiagram"] = rebuild_mermaid_activity_diagram(candidate)
            candidate["sequenceDiagram"] = rebuild_mermaid_sequence_diagram(candidate)

            final_validation = validate_detailed(self.config.schema, candidate)
            return final_validation.data, final_validation.valid, None, None

        baseline = state.last_structurally_valid_candidate or {}
        rename_maps: dict[str, dict[str, str]] = {}
        added_ids_by_section: dict[str, frozenset[str]] = {}

        for section in _INVARIANT_DUPLICATE_CHECK_SECTIONS:
            baseline_items = baseline.get(section) or []
            current_items = candidate.get(section) or []
            if section in _INVARIANT_REQUIREMENT_SECTIONS:
                rename_maps[section] = diff_sections_against_baseline(section, baseline_items, current_items)
            added_ids_by_section[section] = added_ids(section, baseline_items, current_items)

        baseline_entity_names = {
            entity.get("name") for entity in (baseline.get("databaseEntities") or [])
            if isinstance(entity, dict) and entity.get("name")
        }
        current_entity_names = {
            entity.get("name") for entity in (candidate.get("databaseEntities") or [])
            if isinstance(entity, dict) and entity.get("name")
        }
        added_ids_by_section["databaseEntities"] = frozenset(current_entity_names - baseline_entity_names)

        baseline_screens_by_id = {
            str(screen.get("screenId")): screen
            for screen in (baseline.get("uiScreens") or [])
            if isinstance(screen, dict) and screen.get("screenId")
        }

        reconciled, remaining = reconcile_deterministically(
            candidate, issues,
            added_ids_by_section=added_ids_by_section,
            rename_maps_by_section=rename_maps,
            baseline_screens_by_id=baseline_screens_by_id,
        )

        if remaining:
            closure_sections = resolve_minimal_affected_sections(remaining)
            if not closure_sections:
                return reconciled, False, None, None

            remaining_time = deadline - time.monotonic()
            if remaining_time < _MIN_SECONDS_PER_SECTION_REPAIR_ATTEMPT:
                logger.info(
                    "se_documentation.invariant_reconciliation_skipped_insufficient_time "
                    "sections=%s remaining=%.1fs", sorted(closure_sections), remaining_time,
                )
                return reconciled, False, None, None

            synthetic_issues = [
                ReviewerIssue(
                    severity="high",
                    requiresCorrection=True,
                    category="contradiction",
                    affectedField=issue.source_section or "",
                    description=(
                        f"Structural invariant violation ({issue.kind}): "
                        f"section={issue.source_section!r} field={issue.source_field!r} "
                        f"id={issue.invalid_id!r} target={issue.target_section!r}."
                    ),
                    revisionInstruction=(
                        "Fix only this specific mismatch. Preserve every existing id, every "
                        "existing item, and every other field exactly as given -- do not "
                        "renumber, remove, or rename anything not directly required to resolve "
                        "this mismatch."
                    ),
                )
                for issue in remaining
            ]

            closure = RewriteClosure(
                primary_sections=frozenset(closure_sections),
                allowed_sections=frozenset(closure_sections),
            )

            fix_result = guarded_call(
                GuardedCallRequest(
                    stage="rewrite",
                    trusted_parts=writer_trusted_parts,
                    untrusted_parts={
                        **writer_untrusted_parts,
                        "candidate_output": _safe_str(build_compact_rewrite_candidate(reconciled, closure)),
                        "invariant_issues": _safe_str([issue.description for issue in synthetic_issues]),
                    },
                    call_fn=lambda v=reconciled, c=closure, issues_=synthetic_issues: _invoke_with_deadline(
                        self.rewrite_agent.rewrite_targeted,
                        v, c, issues_, context,
                        agent_name=self.agent_name,
                        schema_cls=self.config.schema,
                        deadline=deadline,
                    ),
                    url_mode=self.config.url_mode,
                    allowed_sources=context.allowed_source_metadata,
                ),
                self.firewall,
            )

            if fix_result.provider_failed or fix_result.blocked:
                logger.warning(
                    "se_documentation.invariant_targeted_correction_failed truncated=%s blocked=%s",
                    _is_truncated_failure(fix_result), fix_result.blocked,
                )
                return reconciled, False, fix_result.provider, fix_result.model

            try:
                reconciled = merge_targeted_rewrite(reconciled, fix_result.output, closure)
            except Exception as exc:
                logger.warning("se_documentation.invariant_targeted_correction_merge_rejected reason=%s", exc)
                return reconciled, False, fix_result.provider, fix_result.model

            if diagnose_structural_invariants(reconciled):
                logger.warning("se_documentation.invariant_reconciliation_unresolved_after_targeted_correction")
                return reconciled, False, fix_result.provider, fix_result.model

            reconciled = dict(reconciled)
            reconciled["assumptions"] = rebuild_assumptions_disclosure(reconciled)
            reconciled["traceabilityMatrix"] = rebuild_traceability_matrix(reconciled)
            reconciled["mermaidERD"] = rebuild_mermaid_erd(reconciled)
            reconciled["mermaidClassDiagram"] = rebuild_mermaid_class_diagram(reconciled)
            reconciled["activityDiagram"] = rebuild_mermaid_activity_diagram(reconciled)
            reconciled["sequenceDiagram"] = rebuild_mermaid_sequence_diagram(reconciled)

            final_validation = validate_detailed(self.config.schema, reconciled)
            return final_validation.data, final_validation.valid, fix_result.provider, fix_result.model

        reconciled = dict(reconciled)
        reconciled["assumptions"] = rebuild_assumptions_disclosure(reconciled)
        reconciled["traceabilityMatrix"] = rebuild_traceability_matrix(reconciled)
        reconciled["mermaidERD"] = rebuild_mermaid_erd(reconciled)
        reconciled["mermaidClassDiagram"] = rebuild_mermaid_class_diagram(reconciled)
        reconciled["activityDiagram"] = rebuild_mermaid_activity_diagram(reconciled)
        reconciled["sequenceDiagram"] = rebuild_mermaid_sequence_diagram(reconciled)

        final_validation = validate_detailed(self.config.schema, reconciled)
        return final_validation.data, final_validation.valid, None, None

    def _attempt_one_structural_repair_section(
        self,
        candidate: Candidate,
        closure: RewriteClosure,
        prompt: str,
        validation_errors: list[dict[str, Any]],
        context: ReviewContext,
        writer_trusted_parts: dict[str, str],
        writer_untrusted_parts: dict[str, str],
        deadline: float,
    ) -> GuardedResult:
        """
        One guarded LLM call to repair exactly the single section named by
        `closure.allowed_sections`, plus -- when the call fails specifically
        because the provider's response was truncated (stop_reason/
        finish_reason indicating max_tokens; see
        json_reliability.classify_truncation_failure) and enough of the
        shared absolute `deadline` remains -- ONE retry of that SAME section
        with a larger max_tokens allowance. Never retries a call that failed
        for any other reason, and never retries more than once.
        """
        section_name = next(iter(closure.allowed_sections))

        scoped_kwargs: dict[str, Any] = {}
        if _accepts_keyword(self.rewrite_agent.fix_structure_scoped, "prompt"):
            scoped_kwargs["prompt"] = prompt

        def _call(max_tokens_override: int | None = None) -> GuardedResult:
            kw = dict(scoped_kwargs)
            if max_tokens_override is not None and _accepts_keyword(
                self.rewrite_agent.fix_structure_scoped, "max_tokens_override",
            ):
                kw["max_tokens_override"] = max_tokens_override

            return guarded_call(
                GuardedCallRequest(
                    stage="rewrite",
                    trusted_parts=writer_trusted_parts,
                    untrusted_parts={
                        **writer_untrusted_parts,
                        "invalid_candidate_sections": _safe_str(
                            build_compact_rewrite_candidate(candidate, closure)
                        ),
                        "schema_validation_errors": _safe_str(validation_errors),
                        "repaired_sections": _safe_str(sorted(closure.allowed_sections)),
                    },
                    # No `schema=` here on purpose: the raw response is a
                    # PARTIAL object (only this one section), which would
                    # fail full-candidate-schema validation even when
                    # perfectly correct. The merged, complete candidate is
                    # validated by the caller via the same validate_detailed
                    # the full-repair path uses.
                    call_fn=lambda v=candidate, c=closure, e=validation_errors, kwargs=kw: _invoke_with_deadline(
                        self.rewrite_agent.fix_structure_scoped,
                        v,
                        c,
                        agent_name=self.agent_name,
                        validation_errors=e,
                        schema_cls=self.config.schema,
                        deadline=deadline,
                        **kwargs,
                    ),
                    url_mode=self.config.url_mode,
                    allowed_sources=context.allowed_source_metadata,
                ),
                self.firewall,
            )

        result = _call()

        if result.provider_failed and _is_truncated_failure(result):
            remaining = deadline - time.monotonic()
            if remaining >= _MIN_SECONDS_PER_SECTION_REPAIR_ATTEMPT:
                estimated = estimate_tokens(json.dumps(candidate, default=str))
                retry_max_tokens = max(estimated * 2, 4400)
                logger.info(
                    "se_documentation.structural_repair_section_truncation_retry "
                    "section=%s retry_max_tokens=%d remaining=%.1fs",
                    section_name, retry_max_tokens, remaining,
                )
                result = _call(max_tokens_override=retry_max_tokens)

        return result

    def _attempt_one_semantic_rewrite_group(
        self,
        candidate: Candidate,
        closure: RewriteClosure,
        blocking_issues: list[Any],
        context: ReviewContext,
        writer_trusted_parts: dict[str, str],
        writer_untrusted_parts: dict[str, str],
        deadline: float,
    ) -> GuardedResult:
        """
        One guarded rewrite_targeted call scoped to `closure` (one primary
        section, or a tightly-coupled group sharing the same resolved root
        set -- see group_blocking_issues_by_primary_sections), plus -- same
        contract as _attempt_one_structural_repair_section -- ONE retry with
        a larger max_tokens allowance when (and only when) the failure was a
        confirmed truncation and enough of the shared absolute `deadline`
        remains.
        """
        allowed_rewrite_sections = sorted(closure.allowed_sections)

        def _call(max_tokens_override: int | None = None) -> GuardedResult:
            kwargs: dict[str, Any] = {}
            if max_tokens_override is not None and _accepts_keyword(
                self.rewrite_agent.rewrite_targeted, "max_tokens_override",
            ):
                kwargs["max_tokens_override"] = max_tokens_override

            return guarded_call(
                GuardedCallRequest(
                    stage="rewrite",
                    trusted_parts=writer_trusted_parts,
                    untrusted_parts={
                        **writer_untrusted_parts,
                        "candidate_output": _safe_str(build_compact_rewrite_candidate(candidate, closure)),
                        "reviewer_findings": _safe_str([i.model_dump() for i in blocking_issues]),
                        "allowed_rewrite_sections": _safe_str(allowed_rewrite_sections),
                    },
                    # No `schema=` here on purpose: the raw LLM response is a
                    # PARTIAL object (only this group's allowed sections),
                    # which would fail full-SEDocumentationDto validation
                    # even when perfectly correct. The merged, complete
                    # candidate is schema-validated by the caller instead.
                    call_fn=lambda v=candidate, c=closure, issues=blocking_issues, kw=kwargs: _invoke_with_deadline(
                        self.rewrite_agent.rewrite_targeted,
                        v,
                        c,
                        issues,
                        context,
                        agent_name=self.agent_name,
                        schema_cls=self.config.schema,
                        deadline=deadline,
                        **kw,
                    ),
                    url_mode=self.config.url_mode,
                    allowed_sources=context.allowed_source_metadata,
                ),
                self.firewall,
            )

        result = _call()

        if result.provider_failed and _is_truncated_failure(result):
            remaining = deadline - time.monotonic()
            if remaining >= _MIN_SECONDS_PER_SECTION_REPAIR_ATTEMPT:
                estimated = estimate_tokens(json.dumps(candidate, default=str))
                retry_max_tokens = max(estimated * 2, 4400)
                logger.info(
                    "se_documentation.semantic_rewrite_group_truncation_retry "
                    "sections=%s retry_max_tokens=%d remaining=%.1fs",
                    allowed_rewrite_sections, retry_max_tokens, remaining,
                )
                result = _call(max_tokens_override=retry_max_tokens)

        return result

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
        # SE Documentation has a strict application contract: an unresolved
        # critical finding rejects the NEW candidate completely.  Do not
        # return even a structurally valid candidate as usable/displayable;
        # the .NET application will preserve the previously persisted,
        # accepted document instead.
        #
        # The legacy diagnostic flag remains useful only as a request to
        # capture the rejected candidate through the opt-in, sanitized local
        # snapshot helper.  It no longer disables this rejection gate and it
        # can never make the candidate eligible for an API/application result.
        if self.agent_name == _SE_DOCUMENTATION_AGENT_NAME:
            diagnostic_requested = os.getenv(
                "SE_DOCUMENTATION_CRITICAL_GATE_DISABLED", ""
            ).strip().lower() in ("1", "true", "yes")

            if diagnostic_requested and state.last_structurally_valid_candidate:
                maybe_write_se_documentation_debug_snapshot(
                    state.last_structurally_valid_candidate,
                    request_id=review_run_id,
                )
                logger.warning(
                    "review_pipeline.se_documentation_rejected_diagnostic_snapshot_requested "
                    "agent=%s review_run_id=%s acceptance_gate=enforced",
                    self.agent_name,
                    review_run_id,
                )

            return self._result(
                "rejected", usable=False, output={}, reviewer_findings=findings, decision=decision,
                warning=(
                    "A critical issue remained after the maximum number of rewrites. "
                    "The new document was rejected; any previously accepted document remains unchanged."
                ),
                review_run_id=review_run_id, history=history, attempts=attempt + 1,
            )

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
