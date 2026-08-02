"""
Idea Comparison job worker -- the centralized-job-system flow for
IdeaComparisonAgent, distinct from app/routers/idea_comparison.py's
synchronous POST /compare-generated-ideas endpoint (left completely
unchanged, still has no rewrite-on-semantic-rejection mechanism, still
bounded by its own 45s deadline).

This worker exists because the old 45-second budget was only ever needed to
keep a BLOCKING browser request bounded. The centralized job system already
solves that (the student watches honest real-time progress no matter how
long it takes), so this worker uses a longer 90-second GLOBAL deadline and
spends the extra time on one real quality improvement: when the semantic
reviewer rejects the first draft, its own concrete RevisionInstructions are
used as feedback for exactly one rewrite attempt, instead of immediately
giving up and showing the safe fallback.

Stage boundaries match app/jobs/plans.IDEA_COMPARISON_PLAN
(prepare_context, generate, review, rewrite, final_checks, save), each
backed by an EXPLICIT reporter call from the exact code that knows that
stage finished/was skipped/failed -- never inferred from stage order (see
ProgressReporter's module docstring). "rewrite" and "final_checks" are
marked skipped (not failed) whenever the first review approves -- a
rewrite is a bonus recovery path, not an expected step.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.agents.project_idea_comparison import IdeaComparisonAgent, IdeaComparisonRequest
from app.jobs.reporter import ProgressReporter
from app.routers.ai_jobs import register_agent_job
from app.llm_firewall.firewall import LlmFirewall
from app.review.context import ReviewContext
from app.review.models import AttemptRecord, PipelineResult, ReviewerFindings, ReviewerIssue, RewriteDecision
from app.review.registry import AgentReviewConfig, IdeaComparisonCandidateSchema, get_agent_config
from app.review.review_decision_engine import ReviewDecisionEngine
from app.review.reviewer_agent import ReviewerAgent
from app.review.response import build_review_response
from app.services.llm_provider import ProviderChain

logger = logging.getLogger("fypilot-idea-comparison-job")

# One GLOBAL deadline for the whole job (not per provider call) -- passed
# unchanged into every writer, reviewer, rewrite, and ProviderChain call, so
# remaining time only ever shrinks across the entire flow. The 45s figure on
# the synchronous endpoint is untouched; this is a deliberately larger
# budget made possible by the fact that progress is now streamed live.
_GLOBAL_DEADLINE_SECONDS = 90.0

_MIN_SECONDS_TO_ATTEMPT = 10.0
_MIN_SECONDS_FOR_SYNC_REVIEW = 20.0
_MIN_SECONDS_FOR_REWRITE = 25.0

_firewall = LlmFirewall()
_reviewer_agent = ReviewerAgent(ProviderChain(tier="comparison"))
_decision_engine = ReviewDecisionEngine()


def _build_review_context(request: IdeaComparisonRequest) -> ReviewContext:
    trusted_structural_context = {
        "teamSize": request.teamSize,
        "availableHoursPerWeek": request.availableHoursPerWeek,
        "experienceLevel": request.experienceLevel,
        "skillRatings": request.skillRatings,
    }

    ideas_summary = "\n".join(
        f"- id={idea.id} | {idea.title} | domain={idea.domain} | "
        f"tech={idea.requiredTechnologies} | "
        f"savedScores=(innovation={idea.innovationScore:.0f}, "
        f"feasibility={idea.feasibilityScore:.0f}, market={idea.marketDemandScore:.0f}) | "
        f"{idea.problemStatement[:160]}"
        for idea in request.ideas
    )

    untrusted_project_text = {
        "studentMajor": request.studentMajor,
        "studentSkills": ", ".join(request.studentSkills),
        "ideasSummary": ideas_summary,
    }

    return ReviewContext(
        agent_name="IdeaComparisonAgent",
        trusted_system_instructions=(
            "IdeaComparisonAgent: ranks and qualitatively explains a batch of "
            "the student's own already-generated project ideas against their "
            "skills, team size, and available hours. It has no score fields "
            "at all -- Innovation/Feasibility/MarketDemand/Overall are fixed "
            "values already saved on each idea and must never be restated as "
            "a different number anywhere in this agent's free text. Must "
            "never contradict the student's own trusted skill ratings, and "
            "must only compare the ideas actually provided."
        ),
        trusted_structural_context=trusted_structural_context,
        untrusted_project_text=untrusted_project_text,
        untrusted_user_input="",
        untrusted_conversation_history=[],
        untrusted_existing_code=None,
        untrusted_retrieved_web_content=[],
        previous_model_outputs=[],
        allowed_source_metadata=[],
    )


def _run_reviewer_sync(
    comparison: dict,
    context: ReviewContext,
    agent_config: AgentReviewConfig,
    deadline: float,
    reporter: ProgressReporter,
) -> tuple[str, str, ReviewerFindings | None, RewriteDecision | None, str | None, str | None]:
    """Runs ONLY the semantic Reviewer stage against an already deterministically-validated candidate. Returns (status, warning, findings, decision, provider, model). Never raises -- any provider failure maps to "review_unavailable"."""
    try:
        llm_result = _reviewer_agent.analyze(
            comparison,
            context,
            known_risky_claims=agent_config.known_risky_claims,
            mandatory_fields=agent_config.mandatory_fields,
            extra_rubric=agent_config.extra_rubric,
            deadline=deadline,
            reporter=reporter,
        )
    except Exception:
        logger.exception("IdeaComparisonAgent job: synchronous reviewer call raised.")
        return (
            "review_unavailable",
            "This answer passed automated checks but semantic review failed to run.",
            None, None, None, None,
        )

    if not llm_result.ok or not isinstance(llm_result.data, dict):
        return (
            "review_unavailable",
            "This answer passed automated checks but semantic review did not return a usable result.",
            None, None,
            llm_result.provider if llm_result else None,
            llm_result.model if llm_result else None,
        )

    try:
        findings = ReviewerFindings.model_validate(llm_result.data)
    except Exception:
        return (
            "review_unavailable",
            "This answer passed automated checks but the reviewer's response was invalid.",
            None, None, llm_result.provider, llm_result.model,
        )

    decision = _decision_engine.decide(findings, schema_ok=True)

    if decision.requiresRewrite:
        return (
            "review_rejected",
            f"Semantic review found an issue with this comparison: {decision.reason}",
            findings, decision, llm_result.provider, llm_result.model,
        )

    return ("reviewed", "", findings, decision, llm_result.provider, llm_result.model)


def _skip_stages(reporter: ProgressReporter, stage_keys: list[str], reason: str) -> None:
    for key in stage_keys:
        reporter.skip_stage(key, reason)


def run_idea_comparison_job(request: IdeaComparisonRequest, reporter: ProgressReporter) -> dict[str, Any] | None:
    """Registered under agent name "IdeaComparisonAgent" (see the self-registration call at the bottom of this module)."""
    started_at = time.monotonic()
    deadline = started_at + _GLOBAL_DEADLINE_SECONDS

    agent = IdeaComparisonAgent()
    agent_config = get_agent_config("IdeaComparisonAgent")
    agent.last_llm_used = False
    agent.last_error = None
    agent.last_raw_llm_response = None
    agent.last_provider = None
    agent.last_model_used = None
    agent.last_rewrite_attempted = False

    attempt_history: list[AttemptRecord] = []

    # ── prepare_context ──────────────────────────────────────────────────
    reporter.start_stage("prepare_context")

    ideas = agent._normalize_ideas(request.ideas)

    if not ideas:
        reporter.complete_stage("prepare_context")
        _skip_stages(reporter, ["generate", "review", "rewrite", "final_checks"], "Skipped -- no valid ideas were provided.")
        comparison = agent._empty_response().model_dump()
        return _build_result(agent, comparison, "provider_unavailable",
                              "No generated ideas were provided for comparison.", None, None, [], started_at)

    limited_request = IdeaComparisonRequest(
        studentMajor=request.studentMajor,
        experienceLevel=request.experienceLevel,
        teamSize=request.teamSize,
        availableHoursPerWeek=request.availableHoursPerWeek,
        studentSkills=request.studentSkills,
        skillRatings=request.skillRatings,
        ideas=ideas,
    )

    if deadline - time.monotonic() < _MIN_SECONDS_TO_ATTEMPT:
        reporter.fail_stage("prepare_context", "Not enough time remained to attempt a comparison.")
        _skip_stages(reporter, ["generate", "review", "rewrite", "final_checks"], "Skipped -- insufficient deadline time.")
        comparison = agent.build_safe_fallback(limited_request).model_dump()
        return _build_result(agent, comparison, "provider_unavailable",
                              "Not enough request time remained to attempt a comparison.", None, None, [], started_at)

    context = _build_review_context(limited_request)
    reporter.complete_stage("prepare_context")

    if reporter.is_cancelled():
        return None

    # ── generate ─────────────────────────────────────────────────────────
    reporter.start_stage("generate")

    raw, provider, model, error = agent._call_provider(limited_request, deadline=deadline, reporter=reporter)

    if raw is None:
        agent.last_llm_used = False
        agent.last_error = error or "All AI providers failed to return valid idea comparison JSON."
        reporter.fail_stage("generate", agent.last_error)
        _skip_stages(reporter, ["review", "rewrite", "final_checks"], "Skipped -- no comparison was generated.")
        comparison = agent._safe_unavailable_response(limited_request).model_dump()
        return _build_result(agent, comparison, "provider_unavailable", agent.last_error, None, None, [], started_at)

    agent.last_llm_used = True
    agent.last_provider = provider
    agent.last_model_used = model

    comparison_obj = agent._complete_and_validate(limited_request, raw)
    comparison = comparison_obj.model_dump()

    # Deterministic repetition check is informational only -- the semantic
    # reviewer's own rubric already checks for repetitive_or_generic_comparison
    # and is the single trigger point for a rewrite (see module docstring).
    flagged_ids = agent._find_repetition_issues(comparison_obj, limited_request)
    if flagged_ids:
        logger.info(
            "IdeaComparisonAgent job: deterministic repetition check flagged idea ids %s "
            "(informational only -- the semantic reviewer is the actual rewrite trigger).",
            flagged_ids,
        )

    output_verdict = _firewall.inspect_output(
        comparison, context.untrusted_text_fields(),
        url_mode=agent_config.url_mode, allowed_sources=[], allowed_domains=[],
    )

    if output_verdict.has_blocking_finding():
        logger.warning(
            "IdeaComparisonAgent job: firewall blocked the writer output for a %d-idea batch.",
            len(limited_request.ideas),
        )
        reporter.fail_stage("generate", "The comparison was blocked by the content firewall.")
        _skip_stages(reporter, ["review", "rewrite", "final_checks"], "Skipped -- the content firewall blocked the output.")
        comparison = agent.build_safe_fallback(limited_request).model_dump()
        return _build_result(agent, comparison, "firewall_blocked",
                              "The comparison was blocked by the content firewall.", None, None, [], started_at)

    try:
        IdeaComparisonCandidateSchema.model_validate(comparison)
    except Exception as exc:
        logger.info(
            "IdeaComparisonAgent job: writer output passed the firewall but failed the soft "
            "structural/length check (logged only, still shown): %s", exc,
        )

    reporter.complete_stage("generate")

    if reporter.is_cancelled():
        _skip_stages(reporter, ["review", "rewrite", "final_checks"], "Cancelled.")
        return None

    # ── review (first) ───────────────────────────────────────────────────
    if deadline - time.monotonic() < _MIN_SECONDS_FOR_SYNC_REVIEW:
        reporter.skip_stage("review", "Not enough time remained for a semantic review.")
        _skip_stages(reporter, ["rewrite", "final_checks"], "Skipped -- semantic review did not run.")
        return _build_result(agent, comparison, "automated_checks_passed",
                              "This answer passed automated checks; semantic review was skipped "
                              "because too little request time remained.", None, None, [], started_at)

    reporter.start_stage("review")
    review_status, review_warning, reviewer_findings, decision, reviewer_provider, reviewer_model = _run_reviewer_sync(
        comparison, context, agent_config, deadline, reporter,
    )

    attempt_history.append(AttemptRecord(
        attemptNumber=1,
        stage="writer",
        outputHash="",
        firewallPassed=True,
        schemaValid=True,
        reviewed=review_status in ("reviewed", "review_rejected"),
        reviewerFindings=reviewer_findings,
        decision=decision,
        generatorProvider=agent.last_provider,
        generatorModel=agent.last_model_used,
        reviewerProvider=reviewer_provider,
        reviewerModel=reviewer_model,
        kept=review_status != "review_rejected",
    ))

    if review_status == "review_unavailable":
        reporter.skip_stage("review", review_warning)
        _skip_stages(reporter, ["rewrite", "final_checks"], "Skipped -- semantic review could not complete.")
        return _build_result(agent, comparison, "review_unavailable", review_warning,
                              reviewer_findings, decision, attempt_history, started_at)

    if review_status != "review_rejected":
        reporter.complete_stage("review")
        _skip_stages(reporter, ["rewrite", "final_checks"], "Skipped -- the initial review approved the comparison.")
        return _build_result(agent, comparison, "approved", "", reviewer_findings, decision, attempt_history, started_at)

    # Rejected -- the "review" circle reflects that honestly.
    reporter.fail_stage("review", review_warning)

    blocking_issues: list[ReviewerIssue] = decision.blockingIssues if decision else []
    usable_instructions = [issue for issue in blocking_issues if issue.revisionInstruction.strip()]

    if not usable_instructions:
        reporter.skip_stage("rewrite", "Reviewer found blocking issues but provided no actionable revision instructions.")
        reporter.skip_stage("final_checks", "Skipped -- no rewrite was attempted.")
        comparison = agent.build_safe_fallback(limited_request).model_dump()
        return _build_result(agent, comparison, "review_rejected_safe_fallback", review_warning,
                              reviewer_findings, decision, attempt_history, started_at)

    remaining_for_rewrite = deadline - time.monotonic()
    if remaining_for_rewrite < _MIN_SECONDS_FOR_REWRITE:
        reporter.skip_stage("rewrite", f"Not enough time remained for a revision attempt ({remaining_for_rewrite:.0f}s left).")
        reporter.skip_stage("final_checks", "Skipped -- no rewrite was attempted.")
        comparison = agent.build_safe_fallback(limited_request).model_dump()
        return _build_result(agent, comparison, "rewrite_unavailable_deadline", review_warning,
                              reviewer_findings, decision, attempt_history, started_at)

    if reporter.is_cancelled():
        reporter.skip_stage("rewrite", "Cancelled before the revision attempt started.")
        reporter.skip_stage("final_checks", "Skipped -- no rewrite was attempted.")
        return None

    # ── rewrite (exactly one attempt, using the reviewer's own feedback) ──
    reporter.start_stage("rewrite")
    agent.last_rewrite_attempted = True

    rewrite_prompt = agent.build_rewrite_prompt(limited_request, comparison, blocking_issues)

    try:
        rewrite_result = agent.provider_chain.generate_json(
            rewrite_prompt,
            use_search=False,
            max_tokens=agent._max_tokens_for(limited_request),
            deadline=deadline,
            reporter=reporter,
        )
    except Exception:
        logger.exception("IdeaComparisonAgent job: rewrite call raised.")
        rewrite_result = None

    if rewrite_result is None or not rewrite_result.ok or not isinstance(rewrite_result.data, dict):
        reporter.fail_stage("rewrite", "The revision attempt could not be completed.")
        reporter.skip_stage("final_checks", "Skipped -- the rewrite attempt did not produce a usable result.")
        comparison = agent.build_safe_fallback(limited_request).model_dump()
        return _build_result(agent, comparison, "rewrite_provider_unavailable", review_warning,
                              reviewer_findings, decision, attempt_history, started_at)

    reporter.complete_stage("rewrite")
    agent.last_provider = rewrite_result.provider
    agent.last_model_used = rewrite_result.model

    # ── final_checks (post-rewrite: schema, idea-id/score-consistency, repetition, firewall, ONE final review) ──
    reporter.start_stage("final_checks")

    rewritten_obj = agent._complete_and_validate(limited_request, rewrite_result.data)
    rewritten_comparison = rewritten_obj.model_dump()

    # Idea-id and score-consistency validation are structurally guaranteed,
    # not separate runtime checks: _complete_and_validate matches every
    # result strictly against the AUTHORIZED input idea.id (never trusting
    # an AI-returned id -- see _find_raw_result), and ComparedIdeaQualitative
    # has no score fields at all for a score to be inconsistent about.

    rewrite_flagged_ids = agent._find_repetition_issues(rewritten_obj, limited_request)
    if rewrite_flagged_ids:
        logger.info(
            "IdeaComparisonAgent job: rewritten output still flagged for repetition (idea ids %s) "
            "-- the final semantic review below is the actual gate.", rewrite_flagged_ids,
        )

    rewrite_output_verdict = _firewall.inspect_output(
        rewritten_comparison, context.untrusted_text_fields(),
        url_mode=agent_config.url_mode, allowed_sources=[], allowed_domains=[],
    )

    if rewrite_output_verdict.has_blocking_finding():
        logger.warning(
            "IdeaComparisonAgent job: firewall blocked the REWRITTEN output for a %d-idea batch.",
            len(limited_request.ideas),
        )
        reporter.fail_stage("final_checks", "The revised comparison was blocked by the content firewall.")
        comparison = agent.build_safe_fallback(limited_request).model_dump()
        return _build_result(agent, comparison, "firewall_blocked",
                              "The revised comparison was blocked by the content firewall.",
                              reviewer_findings, decision, attempt_history, started_at)

    try:
        IdeaComparisonCandidateSchema.model_validate(rewritten_comparison)
    except Exception as exc:
        logger.info("IdeaComparisonAgent job: rewritten output failed the soft structural/length check (logged only): %s", exc)

    if deadline - time.monotonic() < _MIN_SECONDS_FOR_SYNC_REVIEW:
        # Never show a rewrite that was never re-verified -- safe fallback,
        # not the original rejected text either.
        reporter.fail_stage("final_checks", "Not enough time remained to re-review the revision.")
        comparison = agent.build_safe_fallback(limited_request).model_dump()
        return _build_result(agent, comparison, "rewrite_unavailable_deadline", review_warning,
                              reviewer_findings, decision, attempt_history, started_at)

    final_status, final_warning, final_findings, final_decision, final_reviewer_provider, final_reviewer_model = _run_reviewer_sync(
        rewritten_comparison, context, agent_config, deadline, reporter,
    )

    attempt_history.append(AttemptRecord(
        attemptNumber=2,
        stage="rewrite",
        outputHash="",
        firewallPassed=True,
        schemaValid=True,
        reviewed=final_status in ("reviewed", "review_rejected"),
        reviewerFindings=final_findings,
        decision=final_decision,
        generatorProvider=agent.last_provider,
        generatorModel=agent.last_model_used,
        reviewerProvider=final_reviewer_provider,
        reviewerModel=final_reviewer_model,
        kept=final_status != "review_rejected",
    ))

    if final_status == "review_unavailable":
        reporter.fail_stage("final_checks", final_warning)
        comparison = agent.build_safe_fallback(limited_request).model_dump()
        return _build_result(agent, comparison, "review_rejected_safe_fallback", final_warning,
                              final_findings, final_decision, attempt_history, started_at)

    if final_status == "review_rejected":
        # Rejected again -- return the safe fallback, never attempt a
        # second rewrite.
        reporter.fail_stage("final_checks", f"The revision was rejected again: {final_warning}")
        comparison = agent.build_safe_fallback(limited_request).model_dump()
        return _build_result(agent, comparison, "review_rejected_safe_fallback", final_warning,
                              final_findings, final_decision, attempt_history, started_at)

    reporter.complete_stage("final_checks")
    return _build_result(agent, rewritten_comparison, "approved_after_revision", "",
                          final_findings, final_decision, attempt_history, started_at)


def _build_result(
    agent: IdeaComparisonAgent,
    comparison: dict,
    review_status: str,
    review_warning: str,
    reviewer_findings: ReviewerFindings | None,
    decision: RewriteDecision | None,
    attempt_history: list[AttemptRecord],
    started_at: float,
) -> dict[str, Any]:
    elapsed_ms = round((time.monotonic() - started_at) * 1000)

    logger.info(
        "IdeaComparisonAgent job: ideas=%s provider=%s model=%s llmUsed=%s "
        "rewriteAttempted=%s reviewStatus=%s elapsedMs=%s",
        comparison.get("totalIdeasCompared"),
        agent.last_provider,
        agent.last_model_used,
        agent.last_llm_used,
        agent.last_rewrite_attempted,
        review_status,
        elapsed_ms,
    )

    usable = review_status in ("approved", "approved_after_revision", "automated_checks_passed", "review_unavailable")

    review_result = PipelineResult(
        status=review_status,  # type: ignore[arg-type]
        usable=usable,
        output=comparison,
        reviewUnavailable=review_status == "review_unavailable",
        warning=review_warning,
        reviewerFindings=reviewer_findings,
        decision=decision,
        attempts=len(attempt_history),
        attemptHistory=attempt_history,
    )

    return {
        "comparison": comparison,
        "agent": "IdeaComparisonAgent",
        "llmUsed": agent.last_llm_used,
        "source": agent.last_provider if agent.last_llm_used else "dynamic-fallback",
        "provider": agent.last_provider,
        "modelUsed": agent.last_model_used,
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "review": build_review_response(review_result),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "message": "Generated ideas compared successfully",
    }


# Self-registration side effect -- runs once this module is imported (see
# app/main.py's AI Agent Jobs router block), mirroring main.py's own
# per-router self-registration style.
register_agent_job("IdeaComparisonAgent", IdeaComparisonRequest, run_idea_comparison_job)
