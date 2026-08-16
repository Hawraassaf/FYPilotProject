"""
FYP Mentor Chat router.

Endpoint:
POST /fyp-chat

This is the pilot feature for the shared AI review pipeline
(app/review/pipeline.py): the mentor's draft answer (the Writer stage) is
generated once, then passes through a content firewall, structural
validation, a semantic Reviewer, a deterministic rewrite decision, and up to
one targeted rewrite before being returned. The other 12 AI agents are not
yet wired into this pipeline -- see app/review/registry.py.

Trivial exchanges (empty message, a bare greeting, a code request without
usable code context) are intentionally never sent to an LLM or the review
pipeline at all -- see FypMentorAgent.try_short_circuit_answer.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter

from app.agents.fyp_mentor_agent import FypMentorAgent, FypMentorRequest
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response, empty_review_response

router = APIRouter(tags=["FYP Mentor Chat"])

logger = logging.getLogger("fypilot-mentor-router")

_MAX_RESPONSE_SOURCES = 6
_MAX_SOURCE_TITLE_LENGTH = 180

# Reserved for the semantic Reviewer / one structural repair / one semantic
# rewrite / final re-review -- the Writer must never consume this. Mentor
# Chat's registry budget (registry.py's FypMentorAgent.max_total_seconds) is
# 90.0s -- the SAME total as Roadmap's single-call budget, so
# _SEMANTIC_REVIEW_RESERVE_SECONDS = 25.0 (routers/roadmap.py) is reused
# unchanged rather than re-derived: it already leaves the Reviewer at least
# one meaningful provider attempt (ProviderChain's shared
# _MIN_SECONDS_PER_PROVIDER_ATTEMPT floor is 4s) plus headroom for the
# deterministic parsing/firewall/schema-validation steps around it, while
# still leaving the Writer the large majority of the 90s budget. Mentor
# Chat's Writer stage CAN also make a search call before generation (see
# FypMentorAgent._should_search_web), the same two-call shape Idea
# Generation's larger 30s reserve was sized for -- but that search step is
# heuristically gated and skipped for the common case (a typical
# project-context question), unlike Idea Generation's unconditional two-call
# sequence, so it does not carry the same risk of routinely eating into a
# larger fixed reserve; when it does run, it shares (never adds to) this
# SAME Writer budget, exactly like Idea Generation's shared-budget search
# step. Previously there was no writer_deadline at all -- chat()'s
# search_web()/generate_json() calls (each able to use their provider's own
# full independent timeout) ran entirely inside the Writer closure passed to
# pipeline.run(), which only self-computed an internal deadline that never
# reached them, so a slow-but-successful candidate could arrive only for
# ReviewPipeline.run's own _time_budget_exceeded check to discard it before
# the Reviewer ever ran.
_WRITER_TIME_RESERVE_SECONDS = 25.0


def _is_safe_source_url(url: str) -> bool:
    """Absolute http(s) only -- rejects javascript:/data:/file:/relative/malformed URLs."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _build_response_sources(mentor_agent: FypMentorAgent, *, usable: bool) -> list[dict[str, str]]:
    """
    Validated, deduplicated, capped copy of the agent's own last_sources --
    a NEW list is always built here, never the mutable agent-owned list
    returned by reference. Only title/url are ever exposed -- never
    snippets, retrieved page text, or prompt content.

    Empty whenever there is no verified evidence actually reflected in the
    final answer: no search ran, the search failed, the retrieved evidence
    was blocked by the firewall, or the pipeline never produced a usable
    answer at all (the deterministic safe fallback doesn't reference the
    search step, so sources would be misleading attached to it).
    """
    if (
        not usable
        or not mentor_agent.last_search_used
        or mentor_agent.last_search_failed
        or mentor_agent.last_search_firewall_blocked
    ):
        return []

    seen_urls: set[str] = set()
    sources: list[dict[str, str]] = []

    for source in mentor_agent.last_sources:
        title = str(source.get("title") or "").strip()[:_MAX_SOURCE_TITLE_LENGTH]
        url = str(source.get("url") or "").strip()

        if not title or not url or not _is_safe_source_url(url):
            continue

        if url in seen_urls:
            continue

        seen_urls.add(url)
        sources.append({"title": title, "url": url})

        if len(sources) >= _MAX_RESPONSE_SOURCES:
            break

    return sources


def _build_review_context(request: FypMentorRequest) -> ReviewContext:
    trusted_structural: dict[str, Any] = {
        "studentProfile.experienceLevel": request.studentProfile.experienceLevel,
        "studentProfile.teamSize": request.studentProfile.teamSize,
        "studentProfile.availableHoursPerWeek": request.studentProfile.availableHoursPerWeek,
        "studentProfile.skillRatings": request.studentProfile.skillRatings,
    }

    untrusted_project_text: dict[str, str] = {
        "studentProfile.major": request.studentProfile.major,
        "studentProfile.skills": ", ".join(request.studentProfile.skills),
    }

    if request.selectedIdea is not None:
        idea = request.selectedIdea
        trusted_structural["selectedIdea.id"] = idea.id
        trusted_structural["selectedIdea.expectedDurationWeeks"] = idea.expectedDurationWeeks
        trusted_structural["selectedIdea.difficultyLevel"] = idea.difficultyLevel
        untrusted_project_text.update(
            {
                "selectedIdea.title": idea.title,
                "selectedIdea.problemStatement": idea.problemStatement,
                "selectedIdea.targetUsers": idea.targetUsers,
                "selectedIdea.whyUseful": idea.whyUseful,
                "selectedIdea.requiredTechnologies": idea.requiredTechnologies,
                "selectedIdea.requiredSkills": idea.requiredSkills,
                "selectedIdea.missingSkills": idea.missingSkills,
                "selectedIdea.domain": idea.domain,
                "selectedIdea.finalDeliverables": idea.finalDeliverables,
            }
        )

    if request.dnaSummary is not None:
        dna = request.dnaSummary
        trusted_structural["dnaSummary.overallScore"] = dna.overallScore
        trusted_structural["dnaSummary.riskLevel"] = dna.riskLevel
        untrusted_project_text["dnaSummary.strengths"] = ", ".join(dna.strengths)
        untrusted_project_text["dnaSummary.weaknesses"] = ", ".join(dna.weaknesses)

    for phase in request.roadmap:
        trusted_structural[f"roadmap.phase{phase.phaseNumber}.isCompleted"] = phase.isCompleted
        untrusted_project_text[f"roadmap.phase{phase.phaseNumber}.name"] = phase.name
        untrusted_project_text[f"roadmap.phase{phase.phaseNumber}.objective"] = phase.objective
        untrusted_project_text[f"roadmap.phase{phase.phaseNumber}.tasks"] = "; ".join(phase.tasks)

    conversation_history = [
        f"{message.role}: {message.content}" for message in request.recentMessages[-8:]
    ]

    previous_model_outputs = [
        message.content for message in request.recentMessages if message.role == "assistant"
    ]

    existing_code = None
    if request.codeContext is not None:
        existing_code = (
            "\n\n".join(
                text
                for text in (
                    request.codeContext.existingCode,
                    request.codeContext.requestedChange,
                )
                if text
            )
            or None
        )

    return ReviewContext(
        agent_name="FypMentorAgent",
        trusted_system_instructions=(
            "FypMentorAgent: a specialized mentor for final year project planning, "
            "implementation, documentation, testing, and defense preparation."
        ),
        trusted_structural_context=trusted_structural,
        untrusted_project_text=untrusted_project_text,
        untrusted_user_input=request.message,
        untrusted_conversation_history=conversation_history,
        untrusted_existing_code=existing_code,
        untrusted_retrieved_web_content=[],
        previous_model_outputs=previous_model_outputs,
        allowed_source_metadata=[],
    )


@router.post("/fyp-chat")
def fyp_chat(request: FypMentorRequest):
    mentor_agent = FypMentorAgent()

    short_circuit = mentor_agent.try_short_circuit_answer(request)

    if short_circuit is not None:
        return {
            "answer": short_circuit.model_dump(),
            "agent": "FypMentorAgent",
            "llmUsed": False,
            "source": "dynamic-fallback",
            "provider": None,
            "modelUsed": None,
            "ollamaError": None,
            "searchUsed": False,
            "searchFailed": False,
            "searchFirewallBlocked": False,
            "searchFirewallFlags": [],
            "searchIntent": None,
            "searchQuery": None,
            "searchQuality": None,
            "searchClassificationSource": None,
            "sources": [],
            "review": empty_review_response(
                "Trivial exchange answered directly; not sent to an LLM or the review pipeline."
            ),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "message": "Mentor response generated successfully",
        }

    context = _build_review_context(request)
    pipeline = ReviewPipeline("FypMentorAgent", tier="mentor")

    # ONE absolute monotonic deadline, computed here from the registry's
    # max_total_seconds (90s) -- passed UNCHANGED to ReviewPipeline (schema/
    # structural-repair/semantic-review/rewrite/final-review all measure
    # against this exact value via ReviewPipeline.run's own deadline
    # threading). The Writer closure gets a SEPARATE, earlier
    # writer_deadline (global - the review reserve above) so it can never
    # consume the time set aside for review -- see
    # _WRITER_TIME_RESERVE_SECONDS's comment for why. Matches Roadmap's
    # identical pattern in routers/roadmap.py.
    global_deadline = time.monotonic() + pipeline.config.max_total_seconds
    writer_deadline = global_deadline - _WRITER_TIME_RESERVE_SECONDS

    logger.info(
        "mentor.request_received writer_budget_seconds=%.1f total_budget_seconds=%.1f",
        writer_deadline - time.monotonic(), pipeline.config.max_total_seconds,
    )

    def _run_writer():
        candidate = mentor_agent.generate_candidate(request, deadline=writer_deadline)

        # generate_candidate() runs chat() internally, which performs the
        # web search lazily -- last_sources is only populated once this
        # call returns. Mutate the list ReviewContext already holds
        # in place (never reassign context.allowed_source_metadata) so the
        # SAME list object guarded_call captured before this closure ran
        # reflects the real sources by the time it runs the output-side
        # URL policy check (see app/review/registry.py's url_mode for
        # FypMentorAgent).
        context.allowed_source_metadata[:] = mentor_agent.last_sources

        return candidate

    result = pipeline.run(
        _run_writer,
        context,
        writer_trusted_parts=context.trusted_text_fields(),
        writer_untrusted_parts=context.untrusted_text_fields(),
        deadline=global_deadline,
    )

    final_answer = (
        result.output if result.usable else mentor_agent.build_safe_fallback(request).model_dump()
    )

    logger.info(
        "mentor.response_built status=%s usable=%s writer_deadline_reason=%s",
        result.status, result.usable, mentor_agent.last_fallback_reason_code,
    )

    return {
        "answer": final_answer,
        "agent": "FypMentorAgent",
        "llmUsed": mentor_agent.last_llm_used,
        "source": mentor_agent.last_provider if mentor_agent.last_llm_used else "dynamic-fallback",
        "provider": mentor_agent.last_provider,
        "modelUsed": mentor_agent.last_model_used,
        "ollamaError": mentor_agent.last_error,
        # Response metadata (NOT persisted anywhere -- no AiOutputReview column
        # backs these fields today) describing the web-search step, if one
        # ran. searchFirewallBlocked/searchFirewallFlags are distinct from
        # searchFailed: a firewall rejection means retrieval succeeded but
        # the content was excluded as unsafe, never a search/provider
        # failure. Flags are rule names only -- never the matched content.
        "searchUsed": mentor_agent.last_search_used,
        "searchFailed": mentor_agent.last_search_failed,
        "searchFirewallBlocked": mentor_agent.last_search_firewall_blocked,
        "searchFirewallFlags": mentor_agent.last_search_firewall_flags,
        # Search-planning diagnostics (see mentor_search_planner.py) --
        # additive fields for observability/debugging; not yet surfaced in
        # the .NET UI (FypMentorServiceResponse would need matching
        # properties first). Safe to add: unknown JSON properties are
        # ignored by the .NET side's case-insensitive deserializer.
        "searchIntent": mentor_agent.last_search_intent,
        "searchQuery": mentor_agent.last_search_query,
        "searchQuality": mentor_agent.last_search_quality,
        "searchClassificationSource": mentor_agent.last_search_classification_source,
        # Structured, backend-validated sources -- never LLM-authored. The
        # model no longer writes citations into suggestedNextActions (see
        # fyp_mentor_agent.py's prompt); the UI renders this list directly.
        "sources": _build_response_sources(mentor_agent, usable=result.usable),
        "review": build_review_response(result),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "message": "Mentor response generated successfully",
    }
