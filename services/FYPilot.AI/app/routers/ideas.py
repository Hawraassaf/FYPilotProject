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

from app.agents.project_idea_agent import ProjectIdeaAgent, StudentProfile


logger = logging.getLogger("fypilot-ideas")

router = APIRouter(tags=["Idea Generation"])


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
        regenerate=regenerate
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


def _response_to_dict(ideas, agent: ProjectIdeaAgent):
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

    if grounded and search_provider == "groq":
        source = "groq-compound-realtime"
    else:
        source = _source_name(
            provider,
            model_used,
            False,
            llm_used
        )

    return {
        "ideas": [
            idea.model_dump()
            if hasattr(idea, "model_dump")
            else idea.dict()
            for idea in ideas
        ],
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
        "sources": sources,
        "sourceCount": len(sources),
        "groundedInLiveData": grounded,

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
        agent = ProjectIdeaAgent()
        ideas = agent.generate_ideas(profile)

        return _response_to_dict(ideas, agent)

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