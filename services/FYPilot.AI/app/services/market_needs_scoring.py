from __future__ import annotations

from app.models.market_needs_models import ScoreBreakdown


def clamp_score(value: object, default: int = 50) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default

    return max(0, min(100, number))


def calculate_demand_score(
    breakdown: ScoreBreakdown,
) -> int:
    """Deterministic current-demand evidence score."""
    weighted = (
        breakdown.problem_evidence * 0.28
        + breakdown.market_fit * 0.25
        + breakdown.university_value * 0.18
        + breakdown.competition_opportunity * 0.14
        + breakdown.technology_momentum * 0.15
    )

    return clamp_score(weighted)


def demand_label(score: int) -> str:
    if score >= 85:
        return "Very High"
    if score >= 70:
        return "High"
    if score >= 50:
        return "Medium"
    return "Low"


def calculate_confidence_score(
    *,
    grounded_in_live_data: bool,
    valid_source_count: int,
    verified_source_count: int,
    problem_evidence_count: int,
    unique_domain_count: int,
) -> int:
    score = 0.0

    if grounded_in_live_data:
        score += 20

    score += min(valid_source_count / 8, 1) * 27
    score += min(verified_source_count / 4, 1) * 25
    score += min(problem_evidence_count / 5, 1) * 15
    score += min(unique_domain_count / 5, 1) * 13

    return clamp_score(score, default=15)


def confidence_label(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"