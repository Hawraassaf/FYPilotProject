# AI Engine — FYPilot Python Service

## Overview

FYPilot.AI is a Python FastAPI microservice that provides AI and machine learning features to the .NET application. It runs independently on port 8000.

**Entry point:** `services/FYPilot.AI/run.py`  
**Framework:** FastAPI + Uvicorn  
**Port:** 8000  
**Docs:** http://localhost:8000/ds/docs

---

## Design Principle

The service is designed to start reliably even if heavy ML packages are not installed:

- **Core endpoints** (`/health`, `/analyze-skills`) — always available, no heavy dependencies
- **ML endpoints** (`/predict-feasibility`, `/check-similarity`, etc.) — use lightweight rule-based fallbacks if scikit-learn/numpy are not available
- **Heavy ML** (`/ds/analytics/*`, `/ds/intelligence/*`) — require full `requirements.txt`

This means the app starts and basic features work even before a full ML environment is set up.

---

## Endpoints

### Core (always available)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service health check |
| POST | `/analyze-skills` | Skill scoring and level recommendation |
| GET | `/ds/health` | Extended health with endpoint list |

### AI Features (lightweight fallback included)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/predict-feasibility` | Score a project's feasibility (0–100) |
| POST | `/check-similarity` | Check if a project idea is original |
| POST | `/match-market` | Match idea to Lebanese market needs |
| POST | `/risk-alarms` | Generate structured project risk alarms |

### Heavy ML (requires full requirements.txt)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/ds/analytics/risk/{project_id}` | Risk factor analysis |
| GET | `/ds/analytics/burndown/{project_id}` | Burndown chart data |
| GET | `/ds/analytics/grade/{project_id}` | Grade prediction |
| POST | `/ds/intelligence/roadmap` | AI roadmap generation |
| POST | `/ds/intelligence/skill-gap` | Skill gap analysis |
| POST | `/ds/intelligence/supervisor-match` | Supervisor matching |

---

## Request / Response Examples

### POST /analyze-skills
```json
Request:
{
  "skills": ["Python", "C#", "SQL", "Machine Learning"],
  "level": "intermediate"
}

Response:
{
  "skillScore": 82,
  "recommendedLevel": "intermediate",
  "message": "Skill analysis completed successfully",
  "analyzedSkills": ["Python", "C#", "SQL", "Machine Learning"],
  "inputLevel": "intermediate"
}
```

### POST /predict-feasibility
```json
Request:
{
  "skill_match_score": 75,
  "missing_skills_count": 2,
  "timeline_weeks": 16,
  "complexity_score": 3,
  "team_size": 1,
  "ai_required": false,
  "dataset_required": false,
  "deployment_required": false,
  "academic_value": 80,
  "market_value": 70
}

Response:
{
  "feasibility_score": 72,
  "risk_level": "medium",
  "explanation": "Project is feasible with manageable risks",
  "top_risk_factors": ["2 missing skills to acquire", "Solo timeline is tight"],
  "suggestions": ["Complete missing skills by week 3", "Use existing datasets"]
}
```

### POST /check-similarity
```json
Request:
{
  "title": "AI-Based Hospital Appointment System",
  "description": "A system using machine learning to schedule hospital appointments"
}

Response:
{
  "similarity_score": 35,
  "originality_score": 65,
  "is_original": true,
  "similar_projects": [...],
  "recommendation": "Your idea is sufficiently original with a novel AI angle"
}
```

### POST /match-market
```json
Request:
{
  "idea_title": "Smart Clinic Booking",
  "idea_description": "AI-powered appointment scheduling for Lebanese clinics",
  "domain": "Healthcare"
}

Response:
{
  "market_relevance_score": 88,
  "best_match_sector": "Healthcare",
  "best_match_problem": "Manual hospital scheduling causes inefficiencies",
  "relevant_keywords": ["hospital", "appointment", "scheduling"],
  "market_insight": "Strong market fit — healthcare digitisation is a key need in Lebanon"
}
```

### POST /risk-alarms
```json
Request:
{
  "skill_match_score": 45,
  "missing_skills_count": 4,
  "timeline_weeks": 12,
  "complexity_score": 4,
  "dataset_required": true,
  "ai_required": true
}

Response:
{
  "alarms": [
    {
      "category": "Skill Gap",
      "severity": "high",
      "reason": "Skill match of 45% is below the recommended 60%",
      "suggested_fix": "Upskill in the top 2 missing areas before week 3"
    }
  ],
  "overall_risk": "high"
}
```

---

## ML Services (internal)

| Service | Description |
|---------|-------------|
| `skill_gap_ml.py` | Identifies gaps between student skills and project needs |
| `grade_predictor.py` | Predicts final FYP grade from progress data |
| `risk_engine.py` | Scores and categorises project risks |
| `roadmap_generator.py` | Builds phased project roadmaps |
| `similarity_checker.py` | Semantic similarity using TF-IDF / sentence-transformers |
| `supervisor_matcher.py` | Matches students with suitable supervisors |
| `burndown_engine.py` | Generates burndown chart data |
| `anomaly_detector.py` | Flags unusual patterns in progress data |
| `analytics_engine.py` | Aggregates analytics across projects |

---

## Sample Datasets

Located in `services/FYPilot.AI/app/data/`:

| File | Description |
|------|-------------|
| `project_ideas.csv` | 50 sample FYP ideas with domain and metadata |
| `previous_fyps.csv` | 50 historical FYP examples for similarity checking |
| `market_needs.csv` | Lebanese industry market needs by sector |
| `feasibility_training.csv` | 150 synthetic rows for ML model training |

> These are **prototype/sample datasets**. The system improves significantly when real historical FYP data from your institution is provided. Replace the CSV files with real data to train better ML models.

---

## Configuration

The .NET app reads the AI service URL from (in priority order):
1. `AI_SERVICE_URL` environment variable
2. `AiService:BaseUrl` in `appsettings.json`
3. Default: `http://localhost:8000`

All calls go through `FYPilot.Infrastructure/Services/AiServiceClient.cs` — the only place in the .NET solution that knows the Python service's URL.

---

## Running the Service

```bash
cd services/FYPilot.AI
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Mac/Linux
pip install -r requirements.txt
python run.py
```

Verify: http://localhost:8000/health → `{"status": "Python AI service running"}`
