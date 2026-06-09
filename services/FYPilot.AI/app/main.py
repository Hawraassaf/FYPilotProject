"""
FYPilot — Python AI & Data Science Service
Lightweight startup: health and skill-analysis endpoints always start.
Heavy ML modules (analytics, intelligence) load gracefully — if numpy/pandas/sklearn
are not installed they are skipped with a warning. Requires Python 3.11+.
"""
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import health

logger = logging.getLogger("fypilot-ds")

app = FastAPI(
    title="FYPilot — Python AI Service",
    description=(
        "Python AI/Data Science backend for FYPilot. "
        "Core: /health, /analyze-skills. "
        "Full ML: /ds/analytics/*, /ds/intelligence/* (requires numpy, pandas, sklearn)."
    ),
    version="1.0.0",
    docs_url="/ds/docs",
    redoc_url="/ds/redoc",
    openapi_url="/ds/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Core endpoints — always available (no heavy dependencies)
app.include_router(health.router)

# ── New AI endpoints (lightweight, no heavy ML required) ──────────────────────
try:
    from app.routers import feasibility
    app.include_router(feasibility.router)
    logger.info("Feasibility router loaded")
except Exception as e:
    logger.warning(f"Feasibility router skipped: {e}")

try:
    from app.routers import similarity
    app.include_router(similarity.router)
    logger.info("Similarity router loaded")
except Exception as e:
    logger.warning(f"Similarity router skipped: {e}")

try:
    from app.routers import market
    app.include_router(market.router)
    logger.info("Market matching router loaded")
except Exception as e:
    logger.warning(f"Market router skipped: {e}")

try:
    from app.routers import risk
    app.include_router(risk.router)
    logger.info("Risk alarms router loaded")
except Exception as e:
    logger.warning(f"Risk router skipped: {e}")

try:
    from app.routers import ideas
    app.include_router(ideas.router)
    logger.info("Ideas (ProjectIdeaAgent) router loaded")
except Exception as e:
    logger.warning(f"Ideas router skipped: {e}")

try:
    from app.routers import dna
    app.include_router(dna.router)
    logger.info("Project DNA router loaded")
except Exception as e:
    logger.warning(f"Project DNA router skipped: {e}")
    
try:
    from app.routers import roadmap
    app.include_router(roadmap.router)
    logger.info("Project Roadmap router loaded")
except Exception as e:
    logger.warning(f"Project Roadmap router skipped: {e}")

try:
    from app.routers import idea_comparison
    app.include_router(idea_comparison.router)
    logger.info("Idea Comparison router loaded")
except Exception as e:
    logger.warning(f"Idea Comparison router skipped: {e}")


try:
    from app.routers import fyp_chat
    app.include_router(fyp_chat.router)
    logger.info("FYP Mentor Chat router loaded")
except Exception as e:
    logger.warning(f"FYP Mentor Chat router skipped: {e}")
    
# Heavy ML routers — loaded only if dependencies are available
try:
    from app.routers import analytics
    app.include_router(analytics.router)
    logger.info("Analytics router loaded successfully")
except ImportError as e:
    logger.warning(f"Analytics router skipped (missing ML dependency): {e}")
    logger.warning("Install full requirements: pip install -r requirements.txt")

try:
    from app.routers import intelligence
    app.include_router(intelligence.router)
    logger.info("Intelligence router loaded successfully")
except ImportError as e:
    logger.warning(f"Intelligence router skipped (missing ML dependency): {e}")


@app.get("/ds/health")
def ds_health():
    return {
        "status": "ok",
        "service": "fypilot-ds-service",
        "version": "1.0.0",
        "note": "Use Python 3.11 and install requirements.txt for full ML features",
        "endpoints": {
            "core": [
                "GET  /health",
                "POST /analyze-skills",
                "GET  /ds/health",
            ],
            "lightweight_ai": [
                "POST /ideas/generate-ideas",
                "POST /predict-feasibility",
                "POST /check-similarity",
                "POST /match-market",
                "POST /risk-alarms",
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
try:
    from app.routers import ollama_test
    app.include_router(ollama_test.router)
    logger.info("Ollama test router loaded")
except Exception as e:
    logger.warning(f"Ollama test router skipped: {e}")