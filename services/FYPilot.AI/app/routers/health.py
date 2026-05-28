from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

router = APIRouter()


class SkillAnalysisRequest(BaseModel):
    skills: List[str]
    level: str = "intermediate"


@router.get("/health")
def health():
    return {"status": "Python AI service running"}


@router.post("/analyze-skills")
def analyze_skills(body: SkillAnalysisRequest):
    level_scores = {"beginner": 40, "intermediate": 70, "advanced": 90, "expert": 100}
    base_score = level_scores.get(body.level.lower(), 70)
    bonus = min(len(body.skills) * 3, 20)
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
        "message": "Skill analysis completed successfully",
        "analyzedSkills": body.skills,
        "inputLevel": body.level,
    }
