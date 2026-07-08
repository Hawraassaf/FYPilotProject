from pydantic import BaseModel
from typing import Optional


# ── Risk Prediction ──────────────────────────────────────────────────────────

class RiskPredictionRequest(BaseModel):
    project_id: int

class RiskFactor(BaseModel):
    factor: str
    severity: str          # low / medium / high / critical
    score: float           # 0-1
    explanation: str

class RiskPredictionResponse(BaseModel):
    project_id: int
    risk_score: float           # 0-100
    risk_level: str             # Low / Medium / High / Critical
    completion_probability: float   # 0-1
    predicted_completion_date: Optional[str]
    days_remaining_estimate: Optional[int]
    risk_factors: list[RiskFactor]
    recommendations: list[str]


# ── Burndown & Velocity ───────────────────────────────────────────────────────

class BurndownPoint(BaseModel):
    date: str
    ideal: float
    actual: float
    remaining: int

class VelocityData(BaseModel):
    week: str
    tasks_completed: int
    story_points: float

class BurndownResponse(BaseModel):
    project_id: int
    total_tasks: int
    completed_tasks: int
    burndown: list[BurndownPoint]
    velocity: list[VelocityData]
    avg_weekly_velocity: float
    estimated_completion_date: Optional[str]
    is_on_track: bool


# ── Grade Prediction ──────────────────────────────────────────────────────────

class GradePredictionResponse(BaseModel):
    project_id: int
    predicted_grade: str       # A / B / C / D / F
    predicted_score: float     # 0-100
    confidence: float          # 0-1
    contributing_factors: dict[str, float]
    improvement_suggestions: list[str]


# ── Supervisor Match ──────────────────────────────────────────────────────────

class SupervisorMatchRequest(BaseModel):
    project_title: str
    project_description: str
    technologies: str

class SupervisorMatch(BaseModel):
    supervisor_id: int
    supervisor_name: str
    match_score: float
    workload: int
    reasons: list[str]

class SupervisorMatchResponse(BaseModel):
    matches: list[SupervisorMatch]


# ── Plagiarism / Similarity ───────────────────────────────────────────────────

class SimilarityRequest(BaseModel):
    project_id: int

class SimilarProject(BaseModel):
    project_id: int
    title: str
    similarity_score: float
    matched_phrases: list[str]

class SimilarityResponse(BaseModel):
    project_id: int
    similarity_results: list[SimilarProject]
    originality_score: float   # 0-100 (higher = more original)
    verdict: str


# ── Anomaly Detection ─────────────────────────────────────────────────────────

class AnomalyAlert(BaseModel):
    type: str
    severity: str
    message: str
    detected_at: str
    suggested_action: str

class AnomalyResponse(BaseModel):
    project_id: int
    anomalies: list[AnomalyAlert]
    health_score: float   # 0-100
    status: str           # healthy / warning / critical


# ── Smart Roadmap Generation ──────────────────────────────────────────────────

class RoadmapPhase(BaseModel):
    phase: int
    name: str
    duration_weeks: int
    start_week: int
    end_week: int
    deliverables: list[str]
    tasks: list[str]
    milestone: str

class RoadmapRequest(BaseModel):
    project_title: str
    project_description: str
    technologies: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    experience_level: str = "intermediate"

class RoadmapResponse(BaseModel):
    total_weeks: int
    phases: list[RoadmapPhase]
    key_milestones: list[str]
    technology_learning_order: list[str]
    weekly_time_commitment: int


# ── Analytics Dashboard ───────────────────────────────────────────────────────

class ProgressTrend(BaseModel):
    date: str
    progress: float

class TaskDistribution(BaseModel):
    status: str
    count: int
    percentage: float

class AnalyticsDashboardResponse(BaseModel):
    project_id: int
    progress_trend: list[ProgressTrend]
    task_distribution: list[TaskDistribution]
    productivity_score: float
    peak_activity_days: list[str]
    total_tasks: int
    completion_rate: float
    avg_task_completion_days: float


# ── Skill Gap Analysis (ML-powered) ──────────────────────────────────────────

class SkillGapRequest(BaseModel):
    current_skills: str
    project_technologies: str
    target_role: Optional[str] = "software engineer"

class SkillGapItem(BaseModel):
    skill: str
    current_level: str    # none / beginner / intermediate / advanced
    required_level: str
    gap_severity: str     # low / medium / high / critical
    learning_time_weeks: int
    resources: list[dict]

class SkillGapResponse(BaseModel):
    gaps: list[SkillGapItem]
    overall_readiness: float   # 0-100
    learning_path: list[str]
    estimated_weeks_to_ready: int


# ── Natural Language Search ───────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5

class SearchResult(BaseModel):
    project_id: int
    title: str
    description: str
    relevance_score: float
    matched_aspects: list[str]

class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    total_found: int
