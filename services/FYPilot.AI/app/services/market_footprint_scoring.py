from __future__ import annotations

from app.models.market_footprint_models import RegionScoreBreakdown
from app.services.market_needs_scoring import clamp_score, demand_label

__all__ = [
    "clamp_score",
    "demand_label",
    "calculate_region_opportunity_score",
    "calculate_evidence_strength",
    "calculate_region_confidence_score",
    "calculate_overall_opportunity_score",
    "calculate_overall_confidence_score",
    "competition_pressure_label",
    "REGION_WEIGHTS",
]

# Lebanon / MENA / Global weighting for the overall opportunity and
# confidence scores. Reflects FYPilot's local-first, regionally-aware
# academic context — a Lebanese student's project is judged first on local
# relevance, then regional, then global reach.
REGION_WEIGHTS: dict[str, float] = {
    "lebanon": 0.40,
    "mena": 0.35,
    "global": 0.25,
}


def calculate_region_opportunity_score(breakdown: RegionScoreBreakdown) -> int:
    """
    Deterministic per-region opportunity score.

    This is an evidence-based, normalized opportunity score — NOT market
    share, revenue, probability of success, or population percentage.
    The LLM supplies the six qualitative dimension ratings; this function
    (not the LLM) performs the actual weighted calculation, clamping, and
    rounding, so the final number is always reproducible from the stored
    breakdown.
    """
    weighted = (
        clamp_score(breakdown.problem_urgency) * 0.25
        + clamp_score(breakdown.geographic_fit) * 0.20
        + clamp_score(breakdown.adoption_readiness) * 0.15
        + clamp_score(breakdown.competition_gap) * 0.15
        + clamp_score(breakdown.target_user_reachability) * 0.10
        + clamp_score(breakdown.technology_momentum) * 0.10
        + clamp_score(breakdown.evidence_strength) * 0.05
    )

    return clamp_score(weighted)


def calculate_evidence_strength(
    *,
    matched_source_count: int,
    verified_source_count: int,
) -> int:
    """
    The evidenceStrength dimension (5% weight) is computed here from actual
    matched sources for the region — never asserted directly by the LLM —
    so a region with no real supporting sources cannot inflate its own
    opportunity score.
    """
    score = 0.0
    score += min(matched_source_count / 4.0, 1.0) * 60.0
    score += min(verified_source_count / 2.0, 1.0) * 40.0
    return clamp_score(score, default=20)


def calculate_region_confidence_score(
    *,
    grounded_in_live_data: bool,
    matched_source_count: int,
    verified_source_count: int,
    unique_domain_count: int,
    has_region_specific_evidence: bool,
) -> int:
    score = 0.0

    if grounded_in_live_data:
        score += 15

    score += min(matched_source_count / 4, 1) * 30
    score += min(verified_source_count / 2, 1) * 25
    score += min(unique_domain_count / 3, 1) * 15
    score += 15 if has_region_specific_evidence else 0

    return clamp_score(score, default=15)


def calculate_overall_opportunity_score(
    region_scores: dict[str, int | None],
) -> int | None:
    """
    Weighted rollup: Lebanon 40% + MENA 35% + Global 25%.

    Returns None (not a fabricated number) if any region score is missing,
    so callers can surface an insufficient-evidence state instead of a
    misleading overall percentage.
    """
    if any(region_scores.get(key) is None for key in REGION_WEIGHTS):
        return None

    weighted = sum(
        region_scores[key] * weight for key, weight in REGION_WEIGHTS.items()
    )

    return clamp_score(weighted)


def calculate_overall_confidence_score(
    region_confidence: dict[str, int],
) -> int:
    weighted = sum(
        region_confidence.get(key, 0) * weight
        for key, weight in REGION_WEIGHTS.items()
    )

    return clamp_score(weighted, default=15)


def competition_pressure_label(competition_gap_score: int) -> str:
    """
    Inverse of the competitionGap dimension: a high gap score means more
    room / less pressure. Reported separately from opportunityScore so the
    two are never confused in the UI.
    """
    if competition_gap_score >= 70:
        return "low"
    if competition_gap_score >= 40:
        return "medium"
    return "high"
