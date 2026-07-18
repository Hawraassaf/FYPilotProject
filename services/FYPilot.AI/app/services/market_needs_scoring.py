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


def calculate_yearly_demand_index(
    *,
    problem_signal: int,
    adoption_signal: int,
    job_demand_signal: int,
    technology_momentum_signal: int,
) -> float:
    """
    A normalized evidence index, not market size or revenue.

    The weights stay fixed so the same inputs always produce the same output.
    """
    weighted = (
        clamp_score(problem_signal) * 0.25
        + clamp_score(adoption_signal) * 0.30
        + clamp_score(job_demand_signal) * 0.25
        + clamp_score(technology_momentum_signal) * 0.20
    )

    return round(max(0.0, min(100.0, weighted)), 2)


def calculate_yearly_confidence(
    *,
    source_count: int,
    verified_source_count: int,
    has_explicit_year_evidence: bool,
    evidence_summary_present: bool,
) -> int:
    score = 0.0
    score += min(source_count / 3.0, 1.0) * 45.0
    score += min(verified_source_count / 2.0, 1.0) * 25.0
    score += 20.0 if has_explicit_year_evidence else 5.0
    score += 10.0 if evidence_summary_present else 0.0
    return clamp_score(score, default=10)


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
    source_backed_year_count: int,
) -> int:
    score = 0.0

    if grounded_in_live_data:
        score += 20

    score += min(valid_source_count / 8, 1) * 20
    score += min(verified_source_count / 4, 1) * 20
    score += min(problem_evidence_count / 5, 1) * 15
    score += min(unique_domain_count / 5, 1) * 10
    score += min(source_backed_year_count / 5, 1) * 15

    return clamp_score(score, default=15)


def confidence_label(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"