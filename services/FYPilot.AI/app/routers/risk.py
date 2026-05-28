"""
POST /risk-alarms
Generates structured risk alarms for a project profile.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class RiskAlarmRequest(BaseModel):
    skill_match_score:    int  = 70
    missing_skills_count: int  = 0
    timeline_weeks:       int  = 16
    complexity_score:     int  = 3
    dataset_required:     bool = False
    ai_required:          bool = False


class RiskAlarmItem(BaseModel):
    category:      str
    severity:      str
    reason:        str
    suggested_fix: str


class RiskAlarmResponse(BaseModel):
    alarms:       list[RiskAlarmItem]
    overall_risk: str


@router.post("/risk-alarms", response_model=RiskAlarmResponse)
def get_risk_alarms(req: RiskAlarmRequest) -> RiskAlarmResponse:
    alarms: list[RiskAlarmItem] = []

    if req.skill_match_score < 50:
        alarms.append(RiskAlarmItem(
            category="Skill Gap",
            severity="high" if req.skill_match_score < 30 else "medium",
            reason=f"Skill match of {req.skill_match_score}% is below the recommended 60%",
            suggested_fix="Upskill in the top 2 missing areas before week 3 or reduce project complexity",
        ))

    if req.missing_skills_count > 3:
        alarms.append(RiskAlarmItem(
            category="Learning Overload",
            severity="high",
            reason=f"{req.missing_skills_count} missing skills must be acquired simultaneously",
            suggested_fix="Prioritise 1-2 critical skills and use libraries/frameworks to bridge others",
        ))

    if req.timeline_weeks < 10:
        alarms.append(RiskAlarmItem(
            category="Timeline",
            severity="high",
            reason=f"{req.timeline_weeks} weeks is very tight for an FYP",
            suggested_fix="Negotiate a 14-16 week timeline or drastically reduce feature scope",
        ))
    elif req.timeline_weeks > 22:
        alarms.append(RiskAlarmItem(
            category="Timeline",
            severity="low",
            reason="Extended timeline may lead to motivation loss or scope creep",
            suggested_fix="Set strict milestone deadlines with supervisor sign-off",
        ))

    if req.complexity_score >= 4:
        alarms.append(RiskAlarmItem(
            category="Complexity",
            severity="medium",
            reason=f"Complexity score of {req.complexity_score}/5 indicates a challenging project",
            suggested_fix="Build a working MVP of the simplest version first, then add complexity",
        ))

    if req.dataset_required and req.skill_match_score < 65:
        alarms.append(RiskAlarmItem(
            category="Data Risk",
            severity="medium",
            reason="Dataset required but skill match suggests limited data handling experience",
            suggested_fix="Use a pre-processed public dataset (Kaggle, UCI) rather than collecting raw data",
        ))

    if req.ai_required and req.skill_match_score < 55:
        alarms.append(RiskAlarmItem(
            category="AI Complexity",
            severity="high",
            reason="AI components require ML expertise that appears insufficient",
            suggested_fix="Use pre-trained models via APIs (OpenAI, HuggingFace) instead of training from scratch",
        ))

    if not alarms:
        alarms.append(RiskAlarmItem(
            category="Overall",
            severity="low",
            reason="No significant risks detected in this project profile",
            suggested_fix="Proceed with your current plan — maintain weekly check-ins",
        ))

    high_count   = sum(1 for a in alarms if a.severity == "high")
    overall_risk = "high" if high_count >= 2 else "medium" if high_count == 1 or len(alarms) > 2 else "low"

    return RiskAlarmResponse(alarms=alarms, overall_risk=overall_risk)
