"""
FYPilot — Python AI & Data Science Service

Lightweight startup:
- Health endpoints always start.
- Working AI routers load safely.
- Optional/heavy routers are skipped gracefully if dependencies/files are missing.

Requires Python 3.11+.
"""

import logging
import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import health
from app.security import verify_api_key


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("fypilot-ds")


app = FastAPI(
    title="FYPilot — Python AI Service",
    dependencies=[Depends(verify_api_key)],
    description=(
        "Python AI/Data Science backend for FYPilot. "
        "Core: /health, /analyze-skills. "
        "Hybrid AI: Groq/Gemini cloud providers, local Ollama fallback, "
        "and trained ML features. "
        "Full ML: /ds/analytics/* and /ds/intelligence/* when dependencies "
        "are installed."
    ),
    version="1.0.0",
    docs_url="/ds/docs",
    redoc_url="/ds/redoc",
    openapi_url="/ds/openapi.json",
)


def get_allowed_origins() -> list[str]:
    """
    SEC-2:
    Do not use a CORS wildcard in production.

    AI_ALLOWED_ORIGINS example:
    http://localhost:5000,http://127.0.0.1:5000
    """

    raw = os.getenv("AI_ALLOWED_ORIGINS", "").strip()

    if raw:
        return [
            origin.strip()
            for origin in raw.split(",")
            if origin.strip()
        ]

    return [
        "http://localhost:5000",
        "http://127.0.0.1:5000",
    ]


allowed_origins = get_allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Internal-Api-Key",
        "X-Request-Id",
    ],
)


# ─────────────────────────────────────────────────────────────────────────────
# Core endpoints — always available
# ─────────────────────────────────────────────────────────────────────────────

app.include_router(health.router)


# ─────────────────────────────────────────────────────────────────────────────
# Hybrid AI routers
# These are loaded safely so one optional feature cannot stop the whole service.
# ─────────────────────────────────────────────────────────────────────────────

try:
    from app.routers import market_needs_router

    # market_needs_router.py exposes:
    # POST /analyze-market-demand      (canonical route)
    # POST /analyze-market-needs       (backward-compatible alias)
    app.include_router(market_needs_router.router)
    logger.info("Market Needs Intelligence router loaded")
except Exception as exception:
    logger.warning(
        "Market Needs Intelligence router skipped: %s",
        exception,
    )
try:
    from app.routers import market_forecast_router

    app.include_router(market_forecast_router.router)
    logger.info("Market Demand Forecasting router loaded")
except Exception as exception:
    logger.warning(
        "Market Demand Forecasting router skipped: %s",
        exception,
    )

try:
    from app.routers import cloud_idea_router

    app.include_router(cloud_idea_router.router)
    logger.info("Cloud Idea Generation router loaded")
except Exception as exception:
    logger.warning(
        "Cloud Idea Generation router skipped: %s",
        exception,
    )


try:
    from app.routers import skill_match_router

    app.include_router(skill_match_router.router)
    logger.info("Skill Match Predictor router loaded")
except Exception as exception:
    logger.warning(
        "Skill Match Predictor router skipped: %s",
        exception,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Existing lightweight AI endpoints
# ─────────────────────────────────────────────────────────────────────────────

try:
    from app.routers import feasibility

    app.include_router(feasibility.router)
    logger.info("Feasibility router loaded")
except Exception as exception:
    logger.warning(
        "Feasibility router skipped: %s",
        exception,
    )


try:
    from app.routers import similarity

    app.include_router(similarity.router)
    logger.info("Similarity router loaded")
except Exception as exception:
    logger.warning(
        "Similarity router skipped: %s",
        exception,
    )


try:
    from app.routers import market

    app.include_router(market.router)
    logger.info("Market matching router loaded")
except Exception as exception:
    logger.warning(
        "Market router skipped: %s",
        exception,
    )


try:
    from app.routers import risk

    app.include_router(risk.router)
    logger.info("Risk alarms router loaded")
except Exception as exception:
    logger.warning(
        "Risk router skipped: %s",
        exception,
    )


try:
    from app.routers import ideas

    app.include_router(ideas.router)
    logger.info("Ideas ProjectIdeaAgent router loaded")
except Exception as exception:
    logger.warning(
        "Ideas router skipped: %s",
        exception,
    )


try:
    from app.routers import dna

    app.include_router(dna.router)
    logger.info("Project DNA router loaded")
except Exception as exception:
    logger.warning(
        "Project DNA router skipped: %s",
        exception,
    )


try:
    from app.routers import roadmap

    app.include_router(roadmap.router)
    logger.info("Project Roadmap router loaded")
except Exception as exception:
    logger.warning(
        "Project Roadmap router skipped: %s",
        exception,
    )


try:
    from app.routers import idea_comparison

    app.include_router(idea_comparison.router)
    logger.info("Idea Comparison router loaded")
except Exception as exception:
    logger.warning(
        "Idea Comparison router skipped: %s",
        exception,
    )


try:
    from app.routers import fyp_chat

    app.include_router(fyp_chat.router)
    logger.info("FYP Mentor Chat router loaded")
except Exception as exception:
    logger.warning(
        "FYP Mentor Chat router skipped: %s",
        exception,
    )


try:
    from app.routers import se_documentation

    app.include_router(se_documentation.router)
    logger.info("SE Documentation router loaded")
except Exception as exception:
    logger.warning(
        "SE Documentation router skipped: %s",
        exception,
    )


try:
    from app.routers import defense_simulator

    app.include_router(defense_simulator.router)
    logger.info("Defense Simulator router loaded")
except Exception as exception:
    logger.warning(
        "Defense Simulator router skipped: %s",
        exception,
    )


try:
    from app.routers import ollama_test

    app.include_router(ollama_test.router)
    logger.info("Ollama test router loaded")
except Exception as exception:
    logger.warning(
        "Ollama test router skipped: %s",
        exception,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Heavy ML routers — loaded only if dependencies are available
# ─────────────────────────────────────────────────────────────────────────────

try:
    from app.routers import analytics

    app.include_router(analytics.router)
    logger.info("Analytics router loaded successfully")
except ImportError as exception:
    logger.warning(
        "Analytics router skipped because an ML dependency is missing: %s",
        exception,
    )
    logger.warning(
        "Install full requirements with: pip install -r requirements.txt"
    )
except Exception as exception:
    logger.warning(
        "Analytics router skipped: %s",
        exception,
    )


try:
    from app.routers import intelligence

    app.include_router(intelligence.router)
    logger.info("Intelligence router loaded successfully")
except ImportError as exception:
    logger.warning(
        "Intelligence router skipped because an ML dependency is missing: %s",
        exception,
    )
except Exception as exception:
    logger.warning(
        "Intelligence router skipped: %s",
        exception,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Service health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/ds/health")
def ds_health():
    return {
        "status": "ok",
        "service": "fypilot-ds-service",
        "version": "1.0.0",
        "note": (
            "Use Python 3.11 and install requirements.txt "
            "for all optional ML features."
        ),
        "architecture": {
            "cloud_ai": (
                "Groq/Gemini provider chain for current-information features"
            ),
            "local_llm": (
                "Ollama fallback for repeated project-context features"
            ),
            "trained_ml": (
                "Skill Match Predictor and analytics when enabled"
            ),
        },
        "endpoints": {
            "core": [
                "GET  /health",
                "POST /analyze-skills",
                "GET  /ds/health",
            ],
            "hybrid_ai": [
                "POST /analyze-market-demand",
                "POST /analyze-market-needs",
                "POST /generate-ideas-cloud",
                "POST /predict-skill-match",
            ],
            "lightweight_ai": [
                "POST /generate-ideas",
                "POST /predict-feasibility",
                "POST /check-similarity",
                "POST /match-market",
                "POST /risk-alarms",
                "POST /analyze-project-dna",
                "POST /fyp-chat",
                "POST /generate-project-roadmap",
                "POST /generate-se-documentation",
                "POST /defense-simulator/generate-questions",
            ],
            "full_ml": [
                "GET  /ds/analytics/risk/{project_id}",
                "GET  /ds/analytics/burndown/{project_id}",
                "GET  /ds/analytics/grade/{project_id}",
                "GET  /ds/analytics/anomalies/{project_id}",
                "POST /ds/intelligence/roadmap",
                "GET  /ds/intelligence/similarity/{project_id}",
                "POST /ds/intelligence/supervisor-match",
                "POST /ds/intelligence/skill-gap",
            ],
        },
    }