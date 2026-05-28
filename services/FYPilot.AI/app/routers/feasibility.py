"""
POST /predict-feasibility
ML-based project feasibility prediction using a trained RandomForestClassifier.
Falls back to rule-based scoring if the model is not yet available.
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

MODEL_PATH = Path(__file__).parent.parent.parent / "data" / "feasibility_model.pkl"


class FeasibilityRequest(BaseModel):
    skill_match_score:     int  = 70
    missing_skills_count:  int  = 1
    timeline_weeks:        int  = 16
    complexity_score:      int  = 3
    team_size:             int  = 1
    ai_required:           bool = False
    dataset_required:      bool = False
    deployment_required:   bool = False
    academic_value:        int  = 80
    market_value:          int  = 75


class FeasibilityResponse(BaseModel):
    feasibility_score:  int
    risk_level:         str
    explanation:        str
    top_risk_factors:   list[str]
    suggestions:        list[str]


def _rule_based(req: FeasibilityRequest) -> FeasibilityResponse:
    """Deterministic rule-based fallback when no ML model is present."""
    score = req.skill_match_score

    # Penalise missing skills
    score -= req.missing_skills_count * 8

    # Penalise aggressive timeline
    if req.timeline_weeks < 8:
        score -= 15
    elif req.timeline_weeks > 20:
        score -= 10

    # Penalise high complexity solo
    if req.complexity_score >= 4 and req.team_size == 1:
        score -= 10

    # AI/dataset risk
    if req.ai_required and req.skill_match_score < 60:
        score -= 12
    if req.dataset_required:
        score -= 8

    # Boost for high value
    score += int((req.academic_value + req.market_value) / 20) - 5

    score = max(10, min(100, score))

    if score >= 75:
        risk = "low"
    elif score >= 50:
        risk = "medium"
    else:
        risk = "high"

    risks: list[str] = []
    if req.missing_skills_count > 2:
        risks.append(f"{req.missing_skills_count} missing skills require upskilling")
    if req.timeline_weeks < 10:
        risks.append("Aggressive timeline — high chance of scope creep")
    if req.complexity_score >= 4 and req.team_size == 1:
        risks.append("Advanced complexity for a solo project")
    if req.ai_required and req.skill_match_score < 60:
        risks.append("AI components with low skill match")
    if req.dataset_required:
        risks.append("Dataset availability not guaranteed")
    if not risks:
        risks.append("Overall project profile looks manageable")

    suggestions = [
        "Start with a minimal proof-of-concept in week 1",
        "Lock MVP scope before week 2",
        "Weekly check-ins with supervisor",
    ]
    if req.missing_skills_count > 0:
        suggestions.insert(0, f"Allocate 2 weeks to address the {req.missing_skills_count} missing skill(s)")

    return FeasibilityResponse(
        feasibility_score=score,
        risk_level=risk,
        explanation=f"Rule-based score of {score}% based on skill match ({req.skill_match_score}%), complexity ({req.complexity_score}/5), and timeline ({req.timeline_weeks} weeks).",
        top_risk_factors=risks,
        suggestions=suggestions,
    )


def _ml_predict(req: FeasibilityRequest) -> FeasibilityResponse:
    with open(MODEL_PATH, "rb") as f:
        model = pickle.load(f)

    features = np.array([[
        req.skill_match_score,
        req.missing_skills_count,
        req.timeline_weeks,
        req.complexity_score,
        req.team_size,
        int(req.ai_required),
        int(req.dataset_required),
        int(req.deployment_required),
        req.academic_value,
        req.market_value,
    ]])

    score = int(model.predict(features)[0])
    score = max(10, min(100, score))
    risk  = "low" if score >= 75 else "medium" if score >= 50 else "high"

    return FeasibilityResponse(
        feasibility_score=score,
        risk_level=risk,
        explanation=f"ML model predicted feasibility of {score}%.",
        top_risk_factors=[],
        suggestions=["Review the project plan with your supervisor."],
    )


@router.post("/predict-feasibility", response_model=FeasibilityResponse)
def predict_feasibility(req: FeasibilityRequest) -> FeasibilityResponse:
    if MODEL_PATH.exists():
        try:
            return _ml_predict(req)
        except Exception:
            pass
    return _rule_based(req)
