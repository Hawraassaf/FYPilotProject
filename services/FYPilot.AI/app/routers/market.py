"""
POST /match-market
Matches a project idea against the Lebanese industry market needs dataset.
"""
from __future__ import annotations

import re
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

MARKET_NEEDS = [
    {"sector": "Healthcare",   "problem": "Manual hospital scheduling causes inefficiencies",  "keywords": ["hospital", "medical", "appointment", "scheduling", "patient", "healthcare"]},
    {"sector": "Education",    "problem": "Lack of AI-adaptive learning tools for students",    "keywords": ["education", "learning", "student", "adaptive", "quiz", "curriculum", "lms"]},
    {"sector": "FinTech",      "problem": "SME access to credit is slow and manual",            "keywords": ["finance", "loan", "credit", "bank", "payment", "invoice", "sme"]},
    {"sector": "Logistics",    "problem": "Last-mile delivery tracking is unreliable",          "keywords": ["delivery", "logistics", "tracking", "transport", "route", "package"]},
    {"sector": "Retail",       "problem": "Lebanese SMEs lack digital storefronts",             "keywords": ["retail", "ecommerce", "store", "product", "inventory", "sales"]},
    {"sector": "Government",   "problem": "Public service applications require manual visits",  "keywords": ["government", "citizen", "public", "permit", "document", "service"]},
    {"sector": "Agriculture",  "problem": "Farmers lack real-time crop monitoring tools",       "keywords": ["agriculture", "farm", "crop", "iot", "sensor", "irrigation", "plant"]},
    {"sector": "Real Estate",  "problem": "Property search is fragmented and inefficient",      "keywords": ["real estate", "property", "rent", "apartment", "listing", "buy"]},
    {"sector": "HR & Talent",  "problem": "SME recruitment is slow and lacks screening tools",  "keywords": ["recruitment", "hr", "hiring", "cv", "job", "candidate", "interview"]},
    {"sector": "Mental Health", "problem": "Mental health support is inaccessible or stigmatised", "keywords": ["mental", "therapy", "wellbeing", "anxiety", "counseling", "health"]},
]


class MarketMatchRequest(BaseModel):
    idea_title:       str
    idea_description: str
    domain:           str


class MarketMatchResponse(BaseModel):
    market_relevance_score: int
    best_match_sector:      str
    best_match_problem:     str
    relevant_keywords:      list[str]
    market_insight:         str


def _keyword_score(text: str, keywords: list[str]) -> float:
    text_lower = text.lower()
    matches    = sum(1 for kw in keywords if kw in text_lower)
    return matches / len(keywords) if keywords else 0.0


@router.post("/match-market", response_model=MarketMatchResponse)
def match_market(req: MarketMatchRequest) -> MarketMatchResponse:
    combined = f"{req.idea_title} {req.idea_description} {req.domain}".lower()

    best_score   = 0.0
    best_need    = MARKET_NEEDS[0]
    matched_kws: list[str] = []

    for need in MARKET_NEEDS:
        score = _keyword_score(combined, need["keywords"])
        if score > best_score:
            best_score = score
            best_need  = need
            matched_kws = [kw for kw in need["keywords"] if kw in combined]

    rel_score = int(min(best_score * 200, 95))  # scale to max 95
    if rel_score < 20:
        rel_score = 20  # base floor

    insight = (
        f"Your project aligns with the {best_need['sector']} sector, addressing: "
        f"'{best_need['problem']}'. "
        f"{'Strong market alignment — this sector is actively seeking technology solutions.' if rel_score >= 60 else 'Consider framing your idea within a market context to strengthen its impact.'}"
    )

    return MarketMatchResponse(
        market_relevance_score=rel_score,
        best_match_sector=best_need["sector"],
        best_match_problem=best_need["problem"],
        relevant_keywords=matched_kws or [req.domain.lower()],
        market_insight=insight,
    )
