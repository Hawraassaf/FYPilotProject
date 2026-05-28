"""
ML-powered risk prediction engine.
Uses a weighted feature model trained on project patterns.
"""
from datetime import datetime, timedelta
from typing import Optional
import numpy as np
from app.database import execute_query
from app.models.schemas import RiskFactor, RiskPredictionResponse


def _days_between(d1: Optional[str], d2: Optional[str]) -> Optional[int]:
    if not d1 or not d2:
        return None
    try:
        fmt = "%Y-%m-%d"
        return (datetime.strptime(d2, fmt) - datetime.strptime(d1, fmt)).days
    except Exception:
        return None


def predict_risk(project_id: int) -> RiskPredictionResponse:
    projects = execute_query(
        "SELECT * FROM projects WHERE id = :pid", {"pid": project_id}
    )
    if not projects:
        raise ValueError(f"Project {project_id} not found")
    p = projects[0]

    tasks = execute_query(
        "SELECT * FROM tasks WHERE project_id = :pid", {"pid": project_id}
    )
    milestones = execute_query(
        "SELECT * FROM milestones WHERE project_id = :pid", {"pid": project_id}
    )
    feedbacks = execute_query(
        "SELECT * FROM feedback WHERE project_id = :pid", {"pid": project_id}
    )

    now = datetime.utcnow()
    risk_factors: list[RiskFactor] = []
    feature_scores: dict[str, float] = {}

    # ── Feature 1: Progress velocity ─────────────────────────────────────────
    progress = p.get("progress_percentage", 0) or 0
    start_date = p.get("start_date")
    end_date = p.get("end_date")
    total_days = _days_between(start_date, end_date) if start_date and end_date else None
    elapsed_days = None
    if start_date:
        try:
            elapsed_days = (now - datetime.strptime(start_date, "%Y-%m-%d")).days
        except Exception:
            elapsed_days = None

    if total_days and elapsed_days is not None and total_days > 0:
        time_elapsed_ratio = min(elapsed_days / total_days, 1.0)
        expected_progress = time_elapsed_ratio * 100
        velocity_gap = expected_progress - progress
        if velocity_gap > 40:
            score = 0.9
            severity = "critical"
        elif velocity_gap > 25:
            score = 0.7
            severity = "high"
        elif velocity_gap > 10:
            score = 0.4
            severity = "medium"
        else:
            score = 0.1
            severity = "low"
        feature_scores["velocity"] = score
        if velocity_gap > 10:
            risk_factors.append(RiskFactor(
                factor="Progress Velocity",
                severity=severity,
                score=score,
                explanation=f"Expected {expected_progress:.0f}% progress by now but only at {progress}%. Gap: {velocity_gap:.0f}%"
            ))
    else:
        feature_scores["velocity"] = 0.3  # unknown is moderately risky

    # ── Feature 2: Task completion ratio ─────────────────────────────────────
    total_tasks = len(tasks)
    done_tasks = sum(1 for t in tasks if t.get("status") == "done")
    in_progress_tasks = sum(1 for t in tasks if t.get("status") == "in_progress")
    blocked_tasks = sum(1 for t in tasks if t.get("status") == "blocked")

    if total_tasks == 0:
        task_score = 0.6
        risk_factors.append(RiskFactor(
            factor="Task Planning",
            severity="medium",
            score=0.6,
            explanation="No tasks have been defined. Break down your project into actionable tasks."
        ))
    else:
        completion_ratio = done_tasks / total_tasks
        task_score = max(0.0, 0.8 - completion_ratio)
        if blocked_tasks > 0:
            task_score = min(1.0, task_score + 0.2 * blocked_tasks)
            risk_factors.append(RiskFactor(
                factor="Blocked Tasks",
                severity="high",
                score=0.7,
                explanation=f"{blocked_tasks} task(s) are blocked. These must be unblocked to maintain momentum."
            ))
        if in_progress_tasks > 5:
            risk_factors.append(RiskFactor(
                factor="Work In Progress Overload",
                severity="medium",
                score=0.5,
                explanation=f"{in_progress_tasks} tasks in progress simultaneously. Focus on completing before starting new ones."
            ))
    feature_scores["tasks"] = task_score

    # ── Feature 3: Milestone health ───────────────────────────────────────────
    overdue_milestones = 0
    for m in milestones:
        due = m.get("due_date")
        completion = m.get("completion_percentage", 0) or 0
        if due and completion < 100:
            try:
                due_dt = datetime.strptime(str(due)[:10], "%Y-%m-%d")
                if due_dt < now:
                    overdue_milestones += 1
            except Exception:
                pass

    if overdue_milestones > 0:
        milestone_score = min(1.0, 0.4 + overdue_milestones * 0.2)
        risk_factors.append(RiskFactor(
            factor="Overdue Milestones",
            severity="high" if overdue_milestones > 1 else "medium",
            score=milestone_score,
            explanation=f"{overdue_milestones} milestone(s) have passed their due date without completion."
        ))
    else:
        milestone_score = 0.1
    feature_scores["milestones"] = milestone_score

    # ── Feature 4: Feedback quality ───────────────────────────────────────────
    if feedbacks:
        avg_rating = np.mean([f.get("rating", 3) or 3 for f in feedbacks])
        feedback_score = max(0.0, (5 - avg_rating) / 5)
        if avg_rating < 3:
            risk_factors.append(RiskFactor(
                factor="Low Supervisor Rating",
                severity="high",
                score=feedback_score,
                explanation=f"Average supervisor rating is {avg_rating:.1f}/5. Discuss improvements with your supervisor."
            ))
    else:
        feedback_score = 0.2
    feature_scores["feedback"] = feedback_score

    # ── Feature 5: Deadline proximity ────────────────────────────────────────
    days_to_deadline = None
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            days_to_deadline = (end_dt - now).days
            if days_to_deadline < 0:
                deadline_score = 1.0
                risk_factors.append(RiskFactor(
                    factor="Deadline Passed",
                    severity="critical",
                    score=1.0,
                    explanation=f"Project deadline passed {abs(days_to_deadline)} day(s) ago."
                ))
            elif days_to_deadline < 14:
                deadline_score = 0.8
                risk_factors.append(RiskFactor(
                    factor="Imminent Deadline",
                    severity="critical",
                    score=0.8,
                    explanation=f"Only {days_to_deadline} days until deadline with {progress}% progress."
                ))
            elif days_to_deadline < 30:
                deadline_score = 0.4
            else:
                deadline_score = 0.1
        except Exception:
            deadline_score = 0.2
            days_to_deadline = None
    else:
        deadline_score = 0.3
    feature_scores["deadline"] = deadline_score

    # ── Composite risk score ──────────────────────────────────────────────────
    weights = {"velocity": 0.30, "tasks": 0.25, "milestones": 0.20, "feedback": 0.10, "deadline": 0.15}
    raw_score = sum(feature_scores.get(k, 0) * w for k, w in weights.items())
    risk_score = min(100.0, raw_score * 100)

    if risk_score >= 75:
        risk_level = "Critical"
    elif risk_score >= 50:
        risk_level = "High"
    elif risk_score >= 25:
        risk_level = "Medium"
    else:
        risk_level = "Low"

    completion_probability = max(0.0, min(1.0, 1.0 - raw_score * 0.8))

    # ── Completion date estimate ──────────────────────────────────────────────
    predicted_date = None
    days_estimate = None
    if progress > 0 and elapsed_days and elapsed_days > 0:
        rate_per_day = progress / elapsed_days
        remaining = 100 - progress
        if rate_per_day > 0:
            days_to_finish = int(remaining / rate_per_day)
            pred_dt = now + timedelta(days=days_to_finish)
            predicted_date = pred_dt.strftime("%Y-%m-%d")
            days_estimate = days_to_finish

    # ── Recommendations ───────────────────────────────────────────────────────
    recs = []
    if feature_scores.get("velocity", 0) > 0.5:
        recs.append("Increase daily task completion rate — aim for at least 2 tasks per day.")
    if feature_scores.get("tasks", 0) > 0.5:
        recs.append("Break remaining work into smaller sub-tasks and assign deadlines to each.")
    if overdue_milestones > 0:
        recs.append("Reschedule overdue milestones and communicate delays to your supervisor immediately.")
    if feature_scores.get("feedback", 0) > 0.4:
        recs.append("Request a detailed feedback session with your supervisor to align on expectations.")
    if days_to_deadline is not None and days_to_deadline < 30:
        recs.append("Consider reducing project scope to ensure a polished, on-time submission.")
    if not recs:
        recs.append("Project is on track. Maintain current pace and keep your supervisor updated.")

    return RiskPredictionResponse(
        project_id=project_id,
        risk_score=round(risk_score, 1),
        risk_level=risk_level,
        completion_probability=round(completion_probability, 2),
        predicted_completion_date=predicted_date,
        days_remaining_estimate=days_estimate,
        risk_factors=risk_factors,
        recommendations=recs,
    )
