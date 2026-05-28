"""
ML-based grade prediction using a weighted feature model.
Predicts FYP grade based on project health signals.
"""
import numpy as np
from app.database import execute_query
from app.models.schemas import GradePredictionResponse


def predict_grade(project_id: int) -> GradePredictionResponse:
    projects = execute_query("SELECT * FROM projects WHERE id = :pid", {"pid": project_id})
    if not projects:
        raise ValueError(f"Project {project_id} not found")
    p = projects[0]

    tasks = execute_query("SELECT * FROM tasks WHERE project_id = :pid", {"pid": project_id})
    milestones = execute_query("SELECT * FROM milestones WHERE project_id = :pid", {"pid": project_id})
    feedbacks = execute_query("SELECT * FROM feedback WHERE project_id = :pid", {"pid": project_id})

    # ── Feature extraction ────────────────────────────────────────────────────
    features: dict[str, float] = {}

    # 1. Overall progress (0-100 → 0-1)
    features["progress"] = (p.get("progress_percentage") or 0) / 100

    # 2. Task completion rate
    total_tasks = len(tasks)
    done_tasks = sum(1 for t in tasks if t.get("status") == "done")
    features["task_completion"] = done_tasks / total_tasks if total_tasks > 0 else 0.0

    # 3. High-priority tasks completed ratio
    high_priority = [t for t in tasks if t.get("priority") == "high"]
    done_high = sum(1 for t in high_priority if t.get("status") == "done")
    features["high_priority_completion"] = done_high / len(high_priority) if high_priority else 0.5

    # 4. Milestone completion
    total_ms = len(milestones)
    avg_ms_completion = float(np.mean([m.get("completion_percentage", 0) or 0 for m in milestones]) / 100) if total_ms > 0 else 0.0
    features["milestone_completion"] = avg_ms_completion

    # 5. Feedback quality
    if feedbacks:
        avg_rating = float(np.mean([f.get("rating", 3) or 3 for f in feedbacks]))
        features["supervisor_rating"] = avg_rating / 5
    else:
        features["supervisor_rating"] = 0.5  # neutral — no data yet

    # 6. Planning quality: number of technologies listed
    techs = p.get("technologies") or ""
    tech_count = len([t.strip() for t in techs.split(",") if t.strip()])
    features["planning_depth"] = min(tech_count / 5, 1.0)

    # 7. Documentation proxy: description length
    desc = p.get("description") or ""
    features["documentation"] = min(len(desc) / 500, 1.0)

    # ── Weighted scoring ──────────────────────────────────────────────────────
    weights = {
        "progress": 0.25,
        "task_completion": 0.20,
        "high_priority_completion": 0.15,
        "milestone_completion": 0.15,
        "supervisor_rating": 0.15,
        "planning_depth": 0.05,
        "documentation": 0.05,
    }

    raw_score = sum(features[k] * weights[k] for k in weights)
    predicted_score = raw_score * 100

    # Confidence: higher when we have more data
    data_points = total_tasks + total_ms + len(feedbacks)
    confidence = min(0.95, 0.4 + data_points * 0.03)

    # Grade mapping
    if predicted_score >= 85:
        grade = "A"
    elif predicted_score >= 75:
        grade = "B"
    elif predicted_score >= 65:
        grade = "C"
    elif predicted_score >= 50:
        grade = "D"
    else:
        grade = "F"

    # Improvement suggestions
    suggestions = []
    if features["task_completion"] < 0.5:
        suggestions.append("Complete more tasks — only {:.0f}% done. Focus on clearing the backlog.".format(features["task_completion"] * 100))
    if features["supervisor_rating"] < 0.6:
        suggestions.append("Work on improving supervisor feedback scores — request a detailed review session.")
    if features["milestone_completion"] < 0.5:
        suggestions.append("Milestone completion is low. Prioritise achieving upcoming milestones on time.")
    if features["documentation"] < 0.5:
        suggestions.append("Expand your project description — thorough documentation signals quality work.")
    if features["high_priority_completion"] < 0.5:
        suggestions.append("Address high-priority tasks first — they carry the most weight.")
    if not suggestions:
        suggestions.append("Excellent progress! Maintain consistency and document your work thoroughly.")

    return GradePredictionResponse(
        project_id=project_id,
        predicted_grade=grade,
        predicted_score=round(predicted_score, 1),
        confidence=round(confidence, 2),
        contributing_factors={k: round(v * 100, 1) for k, v in features.items()},
        improvement_suggestions=suggestions[:4],
    )
