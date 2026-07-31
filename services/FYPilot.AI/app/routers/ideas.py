"""
Idea generation router for FYPilot.

Supports:
- POST /generate-ideas
- POST /ideas/generate-ideas

The router accepts the current .NET request shape and the earlier Copilot
shape, converts them into StudentProfile, and returns provider metadata
without changing the existing ideas payload.
"""

import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.llm_firewall.firewall import LlmFirewall
from app.agents.project_idea_agent import (
    AdminGuidanceContext,
    AdminIdeaGenerationContext,
    FutureOpportunityContext,
    HistoricalProjectContext,
    ProjectIdeaAgent,
    StudentProfile,
)
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response


logger = logging.getLogger("fypilot-ideas")

router = APIRouter(tags=["Idea Generation"])


def _admin_context_firewall_text(profile: StudentProfile) -> str:
    """
    Flattens every piece of actual admin-authored free text (Idea Generation
    Knowledge Base) into one string for the input firewall's injection scan.
    An admin is a privileged user, but their text is still untrusted content
    from the LLM's perspective -- a guidance item, exclusion reason, or
    research gap could accidentally (or deliberately) contain something like
    "ignore all previous instructions", so this must be scanned exactly like
    any other untrusted field, never assumed safe merely because an admin
    submitted it.
    """
    context = profile.adminContext

    if context is None:
        return ""

    parts: list[str] = []

    for item in context.guidance:
        parts.append(f"{item.title}: {item.content}")

    for project in context.previousProjectsToAvoid:
        parts.append(f"{project.title}: {project.problemStatement}")

    for project in context.historicalProjectsForContext:
        parts.append(f"{project.title}: {project.problemStatement}")

    for opportunity in context.futureOpportunities:
        parts.append(f"{opportunity.title}: {opportunity.description} {opportunity.researchGap or ''}".strip())

    return "\n".join(parts)


def _filter_unsafe_admin_context(profile: StudentProfile) -> bool:
    """
    Pre-scans the Idea Generation Knowledge Base context with the same
    central LlmFirewall used everywhere else in the app, BEFORE it ever
    reaches ReviewPipeline -- mirrors the exact pattern ProjectIdeaAgent
    already uses for retrieved web-search evidence (scan, then strip if
    blocked, never fail the whole request).

    This is necessary because ReviewPipeline's own input-firewall pass
    (guarded_call -> LlmFirewall.inspect_prompt) is all-or-nothing: a
    blocking finding there blocks the ENTIRE writer call, which would fail
    the student's whole request over an admin-authored field -- exactly
    what "exclude only the unsafe admin context, continue generating"
    requires we avoid. An admin is a privileged user, but their authored
    text (a guidance item, an exclusion reason, a research gap) is still
    untrusted content from the LLM's perspective and must never be assumed
    safe just because an administrator submitted it.

    Mutates profile.adminContext to None when unsafe content is found and
    returns True; returns False (no mutation) when there's nothing to scan
    or everything is safe.
    """
    if profile.adminContext is None:
        return False

    text = _admin_context_firewall_text(profile)

    if not text:
        return False

    verdict = LlmFirewall().inspect_prompt(
        trusted_parts={},
        untrusted_parts={"admin_curated_context": text},
    )

    if verdict.has_blocking_finding():
        profile.adminContext = None
        return True

    return False


def _build_review_context(profile: StudentProfile) -> ReviewContext:
    trusted_structural_context = {
        "experienceLevel": profile.experienceLevel,
        "targetDifficulty": profile.targetDifficulty,
        "availableHoursPerWeek": profile.availableHoursPerWeek,
        "teamSize": profile.teamSize,
        "skillRatings": profile.skillRatings,
        "lebaneseMarketRelevance": profile.lebaneseMarketRelevance,
        "regenerate": profile.regenerate,
    }

    untrusted_project_text = {
        "major": profile.major,
        "preferredDomain": profile.preferredDomain,
        "studentSkills": ", ".join(profile.studentSkills),
        "projectGoals": ", ".join(profile.projectGoals),
        "previousIdeaTitles": ", ".join(profile.previousIdeaTitles),
        # Idea Generation Knowledge Base -- admin-curated, but still
        # untrusted content from the LLM's perspective (see
        # _admin_context_firewall_text). Empty string when there's no
        # admin context, matching every other untrusted field's shape.
        "untrusted_admin_curated_context": _admin_context_firewall_text(profile),
    }

    return ReviewContext(
        agent_name="ProjectIdeaAgent",
        trusted_system_instructions=(
            "ProjectIdeaAgent: generates exactly 4 personalized final year "
            "project ideas for a student, grounded in a live web-search step "
            "for Lebanese market context. innovationScore, feasibilityScore, "
            "marketDemandScore, expectedDurationWeeks, and difficultyLevel "
            "are always computed deterministically, never written by the LLM."
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


def _get(data: Dict[str, Any], *names: str, default=None):
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return default


def _safe_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default

    return max(minimum, min(number, maximum))


def _normalize_experience(value: Any) -> int:
    """
    Convert .NET string experience into the agent's numeric 0-5 scale.
    """
    if isinstance(value, int):
        return max(0, min(value, 5))

    text = str(value or "intermediate").lower().strip()

    mapping = {
        "beginner": 1,
        "basic": 1,
        "intermediate": 3,
        "advanced": 4,
        "expert": 5
    }

    return mapping.get(text, 3)


def _normalize_difficulty(value: Any) -> int:
    """
    Convert .NET string difficulty into the agent's numeric 1-5 scale.
    """
    if isinstance(value, int):
        return max(1, min(value, 5))

    text = str(value or "medium").lower().strip()

    mapping = {
        "easy": 1,
        "beginner": 1,
        "simple": 2,
        "medium": 3,
        "intermediate": 3,
        "hard": 4,
        "advanced": 4,
        "very hard": 5,
        "expert": 5
    }

    return mapping.get(text, 3)


def _normalize_goals(value: Any) -> List[str]:
    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    if isinstance(value, str) and value.strip():
        return [value.strip()]

    return ["Build a useful final year project"]


def _extract_skills_and_ratings(
    body: Dict[str, Any]
) -> tuple[List[str], Dict[str, int]]:
    """
    Supports the .NET shape:

    "Skills": [
      {"SkillName": "C#", "Rating": 4, "ProficiencyLevel": 4}
    ]

    Also supports the earlier shape:

    "studentSkills": ["Python"]
    "skillRatings": {"Python": 4}
    """
    dotnet_skills = _get(body, "Skills", "skills", default=None)

    student_skills: List[str] = []
    skill_ratings: Dict[str, int] = {}

    if isinstance(dotnet_skills, list):
        for item in dotnet_skills:
            if isinstance(item, dict):
                name = _get(
                    item,
                    "SkillName",
                    "skillName",
                    "name",
                    default=""
                )
                rating = _get(
                    item,
                    "ProficiencyLevel",
                    "proficiencyLevel",
                    "Rating",
                    "rating",
                    default=3
                )
            else:
                name = str(item)
                rating = 3

            name = str(name).strip()

            if not name:
                continue

            normalized_rating = _safe_int(
                rating,
                default=3,
                minimum=1,
                maximum=5
            )

            if name not in student_skills:
                student_skills.append(name)

            skill_ratings[name] = normalized_rating

        return student_skills, skill_ratings

    existing_skills = _get(
        body,
        "studentSkills",
        "StudentSkills",
        default=[]
    )
    existing_ratings = _get(
        body,
        "skillRatings",
        "SkillRatings",
        default={}
    )

    if isinstance(existing_skills, list):
        student_skills = [
            str(skill).strip()
            for skill in existing_skills
            if str(skill).strip()
        ]

    if isinstance(existing_ratings, dict):
        for key, value in existing_ratings.items():
            skill_ratings[str(key)] = _safe_int(
                value,
                default=3,
                minimum=1,
                maximum=5
            )

    for skill in student_skills:
        skill_ratings.setdefault(skill, 3)

    return student_skills, skill_ratings


def _unwrap_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Allow both a flat request and {"profile": {...}}.
    """
    profile = _get(body, "profile", "Profile", default=None)

    if isinstance(profile, dict):
        return profile

    return body


def _build_admin_context(body: Dict[str, Any]) -> AdminIdeaGenerationContext | None:
    """
    Parses the optional Idea Generation Knowledge Base context sent by the
    .NET side (AdminIdeaContextService's AdminIdeaGenerationContextDto,
    already selected/capped there -- this parses it, it does not select
    it). Tolerates both PascalCase (.NET's actual default serialization for
    this endpoint) and camelCase keys, matching every other field in this
    router. Returns None when absent or empty so older requests and an
    empty knowledge base behave exactly as before this feature existed.
    """
    raw = _get(body, "AdminContext", "adminContext", default=None)

    if not isinstance(raw, dict):
        return None

    def _text(item: Dict[str, Any], *names: str) -> str:
        return str(_get(item, *names, default="") or "")

    def _optional_text(item: Dict[str, Any], *names: str) -> str | None:
        value = _get(item, *names, default=None)
        return str(value) if value else None

    guidance = [
        AdminGuidanceContext(
            title=_text(item, "Title", "title"),
            content=_text(item, "Content", "content"),
            guidanceType=_text(item, "GuidanceType", "guidanceType") or "General",
        )
        for item in (_get(raw, "Guidance", "guidance", default=[]) or [])
        if isinstance(item, dict)
    ]

    def _project_list(items: Any) -> List[HistoricalProjectContext]:
        return [
            HistoricalProjectContext(
                title=_text(item, "Title", "title"),
                problemStatement=_text(item, "ProblemStatement", "problemStatement"),
                domain=_optional_text(item, "Domain", "domain"),
                technologies=_text(item, "Technologies", "technologies"),
            )
            for item in (items or [])
            if isinstance(item, dict)
        ]

    previous_projects_to_avoid = _project_list(
        _get(raw, "PreviousProjectsToAvoid", "previousProjectsToAvoid", default=[])
    )
    historical_projects_for_context = _project_list(
        _get(raw, "HistoricalProjectsForContext", "historicalProjectsForContext", default=[])
    )

    future_opportunities = [
        FutureOpportunityContext(
            title=_text(item, "Title", "title"),
            description=_text(item, "Description", "description"),
            suggestedDomain=_optional_text(item, "SuggestedDomain", "suggestedDomain"),
            suggestedTechnologies=_text(item, "SuggestedTechnologies", "suggestedTechnologies"),
            researchGap=_optional_text(item, "ResearchGap", "researchGap"),
            parentProjectTitle=_text(item, "ParentProjectTitle", "parentProjectTitle"),
        )
        for item in (_get(raw, "FutureOpportunities", "futureOpportunities", default=[]) or [])
        if isinstance(item, dict)
    ]

    if not (guidance or previous_projects_to_avoid or historical_projects_for_context or future_opportunities):
        return None

    return AdminIdeaGenerationContext(
        guidance=guidance,
        previousProjectsToAvoid=previous_projects_to_avoid,
        historicalProjectsForContext=historical_projects_for_context,
        futureOpportunities=future_opportunities,
    )


def _build_profile(body: Dict[str, Any]) -> StudentProfile:
    body = _unwrap_body(body)

    student_skills, skill_ratings = _extract_skills_and_ratings(body)

    preferred_domain = _get(
        body,
        "PreferredDomain",
        "preferredDomain",
        default="Web Development"
    )

    previous_titles = _get(
        body,
        "PreviousIdeaTitles",
        "previousIdeaTitles",
        default=[]
    )

    if not isinstance(previous_titles, list):
        previous_titles = []

    regenerate = bool(
        _get(body, "Regenerate", "regenerate", default=False)
    )

    return StudentProfile(
        studentSkills=student_skills,
        skillRatings=skill_ratings,
        major=str(
            _get(
                body,
                "Major",
                "major",
                default="Computer Science"
            )
            or "Computer Science"
        ),
        experienceLevel=_normalize_experience(
            _get(
                body,
                "ExperienceLevel",
                "experienceLevel",
                default="intermediate"
            )
        ),
        preferredDomain=str(
            preferred_domain or "Web Development"
        ),
        targetDifficulty=_normalize_difficulty(
            _get(
                body,
                "TargetDifficulty",
                "targetDifficulty",
                default="medium"
            )
        ),
        availableHoursPerWeek=_safe_int(
            _get(
                body,
                "AvailableHoursPerWeek",
                "availableHoursPerWeek",
                default=10
            ),
            default=10,
            minimum=1,
            maximum=80
        ),
        teamSize=_safe_int(
            _get(
                body,
                "TeamMembers",
                "teamMembers",
                "teamSize",
                default=1
            ),
            default=1,
            minimum=1,
            maximum=10
        ),
        projectGoals=_normalize_goals(
            _get(
                body,
                "ProjectGoals",
                "projectGoals",
                default="Build a useful final year project"
            )
        ),
        lebaneseMarketRelevance=bool(
            _get(
                body,
                "LebaneseMarketRelevance",
                "lebaneseMarketRelevance",
                default=True
            )
        ),
        previousIdeaTitles=[
            str(title).strip()
            for title in previous_titles
            if str(title).strip()
        ],
        regenerate=regenerate,
        adminContext=_build_admin_context(body),
    )


def _source_name(
    provider: str | None,
    model_used: str | None,
    search_used: bool,
    llm_used: bool
) -> str:
    if not llm_used:
        return "dynamic-fallback"

    normalized_model = str(model_used or "").lower()

    if provider == "groq" and search_used and "compound" in normalized_model:
        return "groq-compound-realtime"

    if provider == "groq":
        return "groq-cloud"

    if provider == "gemini" and search_used:
        return "gemini-search"

    if provider == "gemini":
        return "gemini-cloud"

    if provider == "ollama":
        return "ollama-local-fallback"

    return provider or "dynamic-fallback"


def _response_to_dict(
    ideas,
    agent: ProjectIdeaAgent,
    review: Dict[str, Any] | None = None,
    profile: StudentProfile | None = None,
    admin_context_blocked: bool = False,
):
    llm_used = bool(getattr(agent, "last_llm_used", False))

    # Provider/model used to create the structured idea JSON.
    provider = getattr(agent, "last_provider", None)
    model_used = getattr(agent, "last_model_used", None)

    # Separate provider/model used for live evidence retrieval.
    search_provider = getattr(agent, "last_search_provider", None)
    search_model_used = getattr(agent, "last_search_model_used", None)
    search_used = bool(getattr(agent, "last_search_used", False))
    search_failed = bool(getattr(agent, "last_search_failed", False))
    search_error = getattr(agent, "last_search_error", None)

    sources = list(getattr(agent, "last_sources", []) or [])
    grounded = bool(search_used and sources)

    # Firewall status for the retrieved-evidence scan (see ProjectIdeaAgent
    # .generate_ideas()). Response metadata only -- NOT persisted anywhere
    # (no AiOutputReview column backs these fields today). Distinct from
    # search_failed: a firewall block means retrieval succeeded but the
    # content was excluded as unsafe, never a search/provider failure.
    # Flags are rule names only -- never the matched content.
    search_firewall_blocked = bool(getattr(agent, "last_search_firewall_blocked", False))
    search_firewall_flags = list(getattr(agent, "last_search_firewall_flags", []) or [])

    if grounded and search_provider == "groq":
        source = "groq-compound-realtime"
    else:
        source = _source_name(
            provider,
            model_used,
            False,
            llm_used
        )

    # Idea Generation Knowledge Base -- safe, count-only traceability
    # metadata (never full admin text, private exclusion reasons, or
    # database IDs). admin_context_blocked distinguishes "nothing relevant
    # was found/provided" from "something was provided but excluded by the
    # firewall" -- both simply result in adminContextUsed=False here.
    admin_context = profile.adminContext if profile is not None else None
    admin_context_used = admin_context is not None

    return {
        "ideas": [
            idea.model_dump() if hasattr(idea, "model_dump")
            else idea if isinstance(idea, dict)
            else idea.dict()
            for idea in ideas
        ],
        "review": review or {},
        "agent": "ProjectIdeaAgent",
        "llmUsed": llm_used,
        "agentFile": inspect.getfile(ProjectIdeaAgent),

        # Structured generation metadata.
        "source": source,
        "provider": provider,
        "modelUsed": model_used,

        # Live-search metadata.
        "searchUsed": search_used,
        "searchFailed": search_failed,
        "searchProvider": (
            "Groq Compound Mini Web Search"
            if search_provider == "groq"
            else search_provider
        ),
        "searchProviderKey": search_provider,
        "searchModelUsed": search_model_used,
        "searchError": search_error,
        "searchFirewallBlocked": search_firewall_blocked,
        "searchFirewallFlags": search_firewall_flags,
        "sources": sources,
        "sourceCount": len(sources),
        "groundedInLiveData": grounded,

        # Idea Generation Knowledge Base -- see AdminIdeaContextService
        # (.NET) for how these were selected. Count-only; never full admin
        # text, private exclusion reasons, or database IDs.
        "adminContextUsed": admin_context_used,
        "adminContextFirewallBlocked": admin_context_blocked,
        "guidanceItemsUsed": len(admin_context.guidance) if admin_context else 0,
        "historicalProjectsChecked": (
            len(admin_context.previousProjectsToAvoid) + len(admin_context.historicalProjectsForContext)
            if admin_context else 0
        ),
        "excludedProjectsChecked": len(admin_context.previousProjectsToAvoid) if admin_context else 0,
        "futureOpportunitiesConsidered": len(admin_context.futureOpportunities) if admin_context else 0,

        "cloudError": getattr(agent, "last_error", None),

        # Backward-compatible Ollama debug fields.
        "ollamaError": (
            getattr(agent, "last_error", None)
            if provider == "ollama"
            else None
        ),
        "ollamaRawPreview": (
            getattr(agent, "last_raw_llm_response", None)
            if provider == "ollama"
            else None
        ),

        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "message": "Generated 4 project ideas successfully"
    }


@router.post("/generate-ideas")
def generate_ideas(body: Dict[str, Any]):
    try:
        profile = _build_profile(body)

        # Idea Generation Knowledge Base: scan-then-strip BEFORE
        # ReviewPipeline, never fail the whole request over admin-authored
        # text (see _filter_unsafe_admin_context's docstring). A retrieval/
        # parsing problem here must also never break idea generation --
        # this whole block is best-effort.
        admin_context_blocked = False
        try:
            admin_context_blocked = _filter_unsafe_admin_context(profile)
        except Exception:
            logger.warning("Admin idea context firewall scan failed; continuing without it.")
            profile.adminContext = None

        agent = ProjectIdeaAgent()
        context = _build_review_context(profile)
        pipeline = ReviewPipeline("ProjectIdeaAgent", tier="high")
        result = pipeline.run(
            lambda: agent.generate_candidate(profile),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
        )

        ideas_payload = (
            result.output.get("ideas", [])
            if result.usable
            else agent.build_safe_fallback(profile)["ideas"]
        )

        return _response_to_dict(
            ideas_payload,
            agent,
            review=build_review_response(result),
            profile=profile,
            admin_context_blocked=admin_context_blocked,
        )

    except Exception as ex:
        logger.exception("Idea generation endpoint failed.")

        raise HTTPException(
            status_code=500,
            detail=f"Idea generation failed: {str(ex)}"
        ) from ex


@router.post("/ideas/generate-ideas")
def generate_ideas_legacy(body: Dict[str, Any]):
    """
    Keep the earlier endpoint working.
    """
    return generate_ideas(body)