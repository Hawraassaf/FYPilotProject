from fastapi import APIRouter
from typing import Any, Dict, List

router = APIRouter()


@router.get("/health")
def health():
    return {
        "status": "Python AI service running",
        "version": "2.0.0",
        "message": "FYPilot AI service is healthy"
    }


def _get_value(body: Dict[str, Any], *names: str, default=None):
    """
    Reads a value from JSON body using multiple possible property names.
    This makes the endpoint compatible with .NET PascalCase and Python camel/lowercase.
    """
    for name in names:
        if name in body:
            return body[name]
    return default


@router.post("/analyze-skills")
def analyze_skills(body: Dict[str, Any]):
    skills = _get_value(body, "skills", "Skills", "studentSkills", "StudentSkills", default=[])
    level = _get_value(body, "level", "Level", "experienceLevel", "ExperienceLevel", default="intermediate")

    if skills is None:
        skills = []

    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]

    if not isinstance(skills, list):
        skills = []

    normalized_skills: List[str] = [str(s).strip() for s in skills if str(s).strip()]
    normalized_level = str(level or "intermediate").lower().strip()

    level_scores = {
        "beginner": 40,
        "intermediate": 70,
        "advanced": 90,
        "expert": 100
    }

    base_score = level_scores.get(normalized_level, 70)
    bonus = min(len(normalized_skills) * 3, 20)
    skill_score = min(base_score + bonus, 100)

    if skill_score >= 85:
        recommended = "advanced"
    elif skill_score >= 60:
        recommended = "intermediate"
    else:
        recommended = "beginner"

    return {
        "skillScore": skill_score,
        "recommendedLevel": recommended,
        "message": "Skill analysis completed successfully"
    }