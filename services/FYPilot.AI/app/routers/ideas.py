"""
Idea generation router for FYPilot.

Supports:
- POST /generate-ideas          -> preferred endpoint for .NET
- POST /ideas/generate-ideas    -> backward-compatible endpoint from Copilot version

This router accepts the .NET GenerateIdeasRequest shape and adapts it to ProjectIdeaAgent.
"""
import inspect
import logging
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter

from app.agents.project_idea_agent import ProjectIdeaAgent, StudentProfile

logger = logging.getLogger("fypilot-ideas")

router = APIRouter(tags=["Idea Generation"])


def _get(data: Dict[str, Any], *names: str, default=None):
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return default


def _normalize_experience(value: Any) -> int:
    """
    Converts .NET string experience into Copilot agent numeric 0-5.
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
    Converts .NET string difficulty into Copilot agent numeric 1-5.
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
        return [str(x).strip() for x in value if str(x).strip()]

    if isinstance(value, str) and value.strip():
        return [value.strip()]

    return ["Build a useful final year project"]


def _extract_skills_and_ratings(body: Dict[str, Any]) -> tuple[List[str], Dict[str, int]]:
    """
    Supports .NET shape:
    "Skills": [
      {"SkillName": "C#", "Rating": 4, "ProficiencyLevel": 4}
    ]

    Also supports Copilot shape:
    "studentSkills": ["Python"]
    "skillRatings": {"Python": 4}
    """
    dotnet_skills = _get(body, "Skills", "skills", default=None)

    student_skills: List[str] = []
    skill_ratings: Dict[str, int] = {}

    if isinstance(dotnet_skills, list):
        for item in dotnet_skills:
            if isinstance(item, dict):
                name = _get(item, "SkillName", "skillName", "name", default="")
                rating = _get(item, "ProficiencyLevel", "proficiencyLevel", "Rating", "rating", default=3)
            else:
                name = str(item)
                rating = 3

            name = str(name).strip()

            if not name:
                continue

            try:
                rating = int(rating)
            except Exception:
                rating = 3

            rating = max(1, min(rating, 5))

            student_skills.append(name)
            skill_ratings[name] = rating

        return student_skills, skill_ratings

    existing_skills = _get(body, "studentSkills", "StudentSkills", default=[])
    existing_ratings = _get(body, "skillRatings", "SkillRatings", default={})

    if isinstance(existing_skills, list):
        student_skills = [str(s).strip() for s in existing_skills if str(s).strip()]

    if isinstance(existing_ratings, dict):
        for key, value in existing_ratings.items():
            try:
                skill_ratings[str(key)] = max(1, min(int(value), 5))
            except Exception:
                skill_ratings[str(key)] = 3

    for skill in student_skills:
        if skill not in skill_ratings:
            skill_ratings[skill] = 3

    return student_skills, skill_ratings


def _build_profile(body: Dict[str, Any]) -> StudentProfile:
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
        major=_get(body, "Major", "major", default="Computer Science"),
        experienceLevel=_normalize_experience(
            _get(body, "ExperienceLevel", "experienceLevel", default="intermediate")
        ),
        preferredDomain=str(preferred_domain or "Web Development"),
        targetDifficulty=_normalize_difficulty(
            _get(body, "TargetDifficulty", "targetDifficulty", default="medium")
        ),
        availableHoursPerWeek=int(
            _get(body, "AvailableHoursPerWeek", "availableHoursPerWeek", default=10) or 10
        ),
        teamSize=int(
            _get(body, "TeamMembers", "teamMembers", "teamSize", default=1) or 1
        ),
        projectGoals=_normalize_goals(
            _get(body, "ProjectGoals", "projectGoals", default="Build a useful final year project")
        ),
        lebaneseMarketRelevance=True,
        previousIdeaTitles=[str(t).strip() for t in previous_titles if str(t).strip()],
        regenerate=regenerate
    )


def _response_to_dict(ideas, agent=None):
    llm_used = getattr(agent, "last_llm_used", False)

    return {
        "ideas": [
            idea.model_dump() if hasattr(idea, "model_dump") else idea.dict()
            for idea in ideas
        ],
        "agent": "ProjectIdeaAgent",
        "llmUsed": llm_used,
        "agentFile": inspect.getfile(ProjectIdeaAgent),
        "source": "ollama" if llm_used else "dynamic-fallback",
        "ollamaError": getattr(agent, "last_error", None),
        "ollamaRawPreview": getattr(agent, "last_raw_llm_response", None),
        "generatedAt": datetime.now().isoformat(),
        "message": "Generated 6 project ideas successfully"
    }


@router.post("/generate-ideas")
def generate_ideas(body: Dict[str, Any]):
    profile = _build_profile(body)
    agent = ProjectIdeaAgent()
    ideas = agent.generate_ideas(profile)
    

    return _response_to_dict(ideas, agent)


@router.post("/ideas/generate-ideas")
def generate_ideas_legacy(body: Dict[str, Any]):
    """
    Keeps Copilot's earlier endpoint working too.
    """
    return generate_ideas(body)