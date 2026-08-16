"""
Idea Comparison router — exposes IdeaComparisonAgent through FastAPI.

Endpoint:
POST /compare-generated-ideas

End-to-end deadline: the .NET caller sends how much time it's still willing
to wait (X-Request-Deadline-Ms, see AiServiceClient.CompareGeneratedIdeasAsync)
so this request never keeps working after the browser has effectively given
up. That single deadline is resolved once here (_resolve_deadline) and
threaded through the writer call (IdeaComparisonAgent.compare) and the
conditional synchronous Reviewer call below.

Semantic review is intentionally decoupled from this endpoint's ability to
render a result -- this does NOT call ReviewPipeline.run() (Writer+Reviewer
sequential, both real ~15-30s DeepInfra/Groq calls). Two full sequential
calls routinely pushed a single request past the 45s deadline (observed:
46-152s for 4 ideas, 45.6s even for the minimum 2-idea batch), and
pipeline.run() has no way to abort a Reviewer call already in flight, so a
slow/rate-limited Reviewer silently discarded an already-good Writer result
(winner/ranking cards came back null even though the AI had produced a valid
comparison). The flow here instead is:

1. One writer call: IdeaComparisonAgent.compare() (shared
   ProviderChain(tier="comparison"), deterministic ID-trust/rank-permutation/
   winner=rank1 completion, and its own single repetition-retry, all already
   enforced inside that method -- see project_idea_comparison.py).
2. Deterministic validation on the writer's output: an explicit firewall
   scan (the one genuinely new check versus what compare() already does),
   plus a soft/logged structural-length check (IdeaComparisonCandidateSchema)
   that never discards an otherwise-good comparison just for running a few
   words past a length guideline -- rank/id/winner invariants are already
   guaranteed by construction, not something this check can meaningfully add.
3. Once those checks pass, `comparison` IS the response payload -- nothing
   downstream can un-set it except a genuine firewall block or a completed
   (not timed-out) Reviewer call that actually finds a blocking issue.
4. The semantic Reviewer runs ONLY when a clearly sufficient amount of the
   deadline remains after step 1-2 (_MIN_SECONDS_FOR_SYNC_REVIEW) -- and
   only as a single Reviewer call, never a Rewrite loop. Timeout, provider
   failure, rate limiting, or an exception in that call all map to
   "review_unavailable" and leave `comparison` untouched; only a completed
   review that actually finds a blocking issue swaps in the safe fallback
   ("review_rejected"). See _run_reviewer_sync.
"""

import dataclasses
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Header

from app.agents.project_idea_comparison import (
    IdeaComparisonAgent,
    IdeaComparisonRequest,
)
from app.llm_firewall.firewall import LlmFirewall
from app.review.context import ReviewContext
from app.review.models import AttemptRecord, PipelineResult, ReviewerFindings, RewriteDecision
from app.review.registry import AgentReviewConfig, IdeaComparisonCandidateSchema, get_agent_config
from app.review.review_decision_engine import ReviewDecisionEngine
from app.review.reviewer_agent import ReviewerAgent
from app.review.response import build_review_response
from app.services.llm_provider import ProviderChain

logger = logging.getLogger("fypilot-idea-comparison-agent")

router = APIRouter(tags=["Idea Comparison"])

# Used when the caller sends no deadline header (e.g. a direct API call) or
# an unparseable one -- matches the .NET default (see AiServiceClient).
_DEFAULT_DEADLINE_SECONDS = 45.0
_MIN_DEADLINE_SECONDS = 5.0
_MAX_DEADLINE_SECONDS = 60.0
# Below this many remaining deadline seconds, skip the writer call entirely
# and go straight to the safe fallback rather than starting a call that
# can't finish in time.
_MIN_SECONDS_TO_ATTEMPT = 8.0
# The Reviewer stage runs the same "comparison" tier as the writer (~15-30s
# per real DeepInfra/Groq attempt, measured against this endpoint's actual
# prompt sizes) -- only start it when this many seconds remain after the
# writer call, so a full-length Reviewer attempt still leaves a safety
# margin before .NET's own hard cutoff. Below this, the writer's
# already-validated result is returned immediately instead (status
# "automated_checks_passed") -- see the module docstring.
_MIN_SECONDS_FOR_SYNC_REVIEW = 20.0

# Reused across requests -- both are stateless (ProviderChain only resolves
# tier config; LlmFirewall/ReviewDecisionEngine hold no per-request state),
# same pattern ReviewPipeline itself uses internally.
_firewall = LlmFirewall()
_reviewer_agent = ReviewerAgent(ProviderChain(tier="comparison"))
_decision_engine = ReviewDecisionEngine()


def _resolve_deadline(started_at: float, header_value: str | None) -> float:
    try:
        requested_seconds = (
            float(header_value) / 1000.0 if header_value else _DEFAULT_DEADLINE_SECONDS
        )
    except (TypeError, ValueError):
        requested_seconds = _DEFAULT_DEADLINE_SECONDS

    clamped_seconds = max(_MIN_DEADLINE_SECONDS, min(requested_seconds, _MAX_DEADLINE_SECONDS))
    return started_at + clamped_seconds


def _build_review_context(request: IdeaComparisonRequest) -> ReviewContext:
    trusted_structural_context = {
        "teamSize": request.teamSize,
        "availableHoursPerWeek": request.availableHoursPerWeek,
        "experienceLevel": request.experienceLevel,
        "skillRatings": request.skillRatings,
    }

    # Just enough per idea for the Reviewer's actual checks (domain/tech
    # alignment, fabricated-score detection, groundedness) -- not every
    # field. A larger context here directly adds to the Reviewer's own
    # latency, which counts against the same end-to-end deadline as the
    # writer call (see _resolve_deadline).
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


@dataclasses.dataclass
class _SyncReviewOutcome:
    status: str
    warning: str
    findings: ReviewerFindings | None = None
    decision: RewriteDecision | None = None
    provider: str | None = None
    model: str | None = None


def _run_reviewer_sync(
    comparison: dict,
    context: ReviewContext,
    agent_config: AgentReviewConfig,
    deadline: float,
) -> _SyncReviewOutcome:
    """
    Runs ONLY the semantic Reviewer stage (no Writer call, no Rewrite loop)
    against an already deterministically-validated writer result. Never
    raises -- any provider failure, malformed response, or unexpected
    exception maps to "review_unavailable" so the caller keeps showing
    `comparison` unchanged. Only a *completed* review that actually finds a
    blocking issue returns "review_rejected".
    """
    try:
        llm_result = _reviewer_agent.analyze(
            comparison,
            context,
            known_risky_claims=agent_config.known_risky_claims,
            mandatory_fields=agent_config.mandatory_fields,
            extra_rubric=agent_config.extra_rubric,
            deadline=deadline,
        )
    except Exception:
        logger.exception("IdeaComparisonAgent: synchronous reviewer call raised.")
        return _SyncReviewOutcome(
            status="review_unavailable",
            warning="This answer passed automated checks but semantic review failed to run.",
        )

    if not llm_result.ok or not isinstance(llm_result.data, dict):
        return _SyncReviewOutcome(
            status="review_unavailable",
            warning=(
                "This answer passed automated checks but semantic review did not "
                "return a usable result."
            ),
            provider=llm_result.provider if llm_result else None,
            model=llm_result.model if llm_result else None,
        )

    try:
        findings = ReviewerFindings.model_validate(llm_result.data)
    except Exception:
        return _SyncReviewOutcome(
            status="review_unavailable",
            warning="This answer passed automated checks but the reviewer's response was invalid.",
            provider=llm_result.provider,
            model=llm_result.model,
        )

    decision = _decision_engine.decide(findings, schema_ok=True)

    if decision.requiresRewrite:
        return _SyncReviewOutcome(
            status="review_rejected",
            warning=f"Semantic review found an issue with this comparison: {decision.reason}",
            findings=findings,
            decision=decision,
            provider=llm_result.provider,
            model=llm_result.model,
        )

    return _SyncReviewOutcome(
        status="reviewed",
        warning="",
        findings=findings,
        decision=decision,
        provider=llm_result.provider,
        model=llm_result.model,
    )


@router.post("/compare-generated-ideas")
def compare_generated_ideas(
    request: IdeaComparisonRequest,
    x_request_deadline_ms: str | None = Header(default=None, alias="X-Request-Deadline-Ms"),
):
    started_at = time.monotonic()
    deadline = _resolve_deadline(started_at, x_request_deadline_ms)

    agent = IdeaComparisonAgent()
    agent_config = get_agent_config("IdeaComparisonAgent")

    review_status = "provider_unavailable"
    review_warning = ""
    reviewer_findings: ReviewerFindings | None = None
    decision: RewriteDecision | None = None
    attempt_history: list[AttemptRecord] = []

    remaining = deadline - time.monotonic()

    if remaining < _MIN_SECONDS_TO_ATTEMPT:
        logger.info(
            "IdeaComparisonAgent: only %.1fs left on the request deadline before any "
            "provider call -- returning the safe fallback without attempting one.",
            remaining,
        )
        comparison = agent.build_safe_fallback(request).model_dump()
        review_warning = "Not enough request time remained to attempt a comparison."
    else:
        # The ONE cloud writer call this endpoint requires before it can
        # render anything -- compare() already runs the shared
        # ProviderChain("comparison" tier), deterministic ID/rank/winner
        # completion, and its own single repetition-retry internally.
        result_obj = agent.compare(request, deadline=deadline)
        comparison = result_obj.model_dump()

        if not agent.last_llm_used:
            # compare() already fell back internally (every provider
            # failed, or output stayed repetitive/generic after one retry)
            # -- `comparison` here IS build_safe_fallback()'s own output.
            review_warning = agent.last_error or "No AI provider produced a comparison."
        else:
            context = _build_review_context(request)

            output_verdict = _firewall.inspect_output(
                comparison,
                context.untrusted_text_fields(),
                url_mode=agent_config.url_mode,
                allowed_sources=[],
                allowed_domains=[],
            )

            if output_verdict.has_blocking_finding():
                logger.warning(
                    "IdeaComparisonAgent: firewall blocked the writer output for a "
                    "%d-idea batch -- returning the safe fallback instead.",
                    len(request.ideas),
                )
                comparison = agent.build_safe_fallback(request).model_dump()
                review_status = "firewall_blocked"
                review_warning = "The comparison was blocked by the content firewall."
            else:
                # Soft/logged structural check only -- rank permutation and
                # winner=rank1 are already guaranteed by construction in
                # IdeaComparisonAgent._complete_and_validate, so the only
                # thing this can realistically still catch is a field
                # running past its word-count guideline. Never discards an
                # otherwise-good, idea-specific comparison over that.
                try:
                    IdeaComparisonCandidateSchema.model_validate(comparison)
                except Exception as exc:
                    logger.info(
                        "IdeaComparisonAgent: writer output passed the firewall but failed "
                        "the soft structural/length check (logged only, still shown): %s", exc,
                    )

                remaining_for_review = deadline - time.monotonic()

                if remaining_for_review >= _MIN_SECONDS_FOR_SYNC_REVIEW:
                    outcome = _run_reviewer_sync(comparison, context, agent_config, deadline)
                    review_status = outcome.status
                    review_warning = outcome.warning
                    reviewer_findings = outcome.findings
                    decision = outcome.decision
                    attempt_history = [
                        AttemptRecord(
                            attemptNumber=1,
                            stage="writer",
                            outputHash="",
                            firewallPassed=True,
                            schemaValid=True,
                            reviewed=outcome.status in ("reviewed", "review_rejected"),
                            reviewerFindings=outcome.findings,
                            decision=outcome.decision,
                            generatorProvider=agent.last_provider,
                            generatorModel=agent.last_model_used,
                            reviewerProvider=outcome.provider,
                            reviewerModel=outcome.model,
                            kept=outcome.status != "review_rejected",
                        )
                    ]

                    if outcome.status == "review_rejected":
                        logger.warning(
                            "IdeaComparisonAgent: synchronous reviewer rejected the writer "
                            "output for a %d-idea batch -- returning the safe fallback instead.",
                            len(request.ideas),
                        )
                        comparison = agent.build_safe_fallback(request).model_dump()
                else:
                    logger.info(
                        "IdeaComparisonAgent: only %.1fs left after the writer call -- "
                        "skipping the synchronous reviewer for this %d-idea batch.",
                        remaining_for_review,
                        len(request.ideas),
                    )
                    review_status = "automated_checks_passed"
                    review_warning = (
                        "This answer passed automated checks; semantic review was skipped "
                        "because too little request time remained."
                    )

    elapsed_ms = round((time.monotonic() - started_at) * 1000)

    # Safe metadata only -- agent name, provider/model, elapsed time,
    # fallback/rewrite flags, review status. Never API keys, full prompts,
    # or raw model output.
    logger.info(
        "IdeaComparisonAgent: ideas=%s provider=%s model=%s llmUsed=%s "
        "rewriteAttempted=%s reviewStatus=%s elapsedMs=%s",
        len(request.ideas),
        agent.last_provider,
        agent.last_model_used,
        agent.last_llm_used,
        agent.last_rewrite_attempted,
        review_status,
        elapsed_ms,
    )

    usable = review_status not in ("provider_unavailable", "firewall_blocked", "review_rejected")

    # PipelineResult's own validator requires usable=False to carry an
    # EMPTY output (see app/review/models.py) -- every agent going through
    # the shared ReviewPipeline already satisfies this (pipeline.py's
    # _result() helper does), but this endpoint builds its own
    # PipelineResult by hand and was passing the real `comparison` dict
    # (by this point always non-empty -- either the writer's candidate or
    # agent.build_safe_fallback()'s own non-empty template) regardless of
    # `usable`. Live-reproduced as an unhandled 500 (a pydantic
    # ValidationError bubbling out of the route) whenever review_status
    # landed on any of the three "not usable" values above -- e.g. when
    # IdeaComparisonAgent.compare()'s own internal repetition-retry gives
    # up and falls back internally (review_status then stays at its
    # "provider_unavailable" default, comparison holds the fallback's
    # non-empty dict). This `review` field only ever describes the
    # separate top-level "comparison" key below for display purposes, so
    # emptying it here changes no other behavior.
    review_result = PipelineResult(
        status=review_status,  # type: ignore[arg-type]
        usable=usable,
        output=comparison if usable else {},
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
