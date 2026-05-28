"""
Anomaly detection for student project health.
Detects unusual patterns that may indicate a struggling student.
"""
from datetime import datetime, timedelta
import numpy as np
from app.database import execute_query
from app.models.schemas import AnomalyAlert, AnomalyResponse


def detect_anomalies(project_id: int) -> AnomalyResponse:
    projects = execute_query("SELECT * FROM projects WHERE id = :pid", {"pid": project_id})
    if not projects:
        raise ValueError(f"Project {project_id} not found")
    p = projects[0]

    tasks = execute_query("SELECT * FROM tasks WHERE project_id = :pid ORDER BY created_at", {"pid": project_id})
    milestones = execute_query("SELECT * FROM milestones WHERE project_id = :pid", {"pid": project_id})

    now = datetime.utcnow()
    anomalies: list[AnomalyAlert] = []
    health_penalties: list[float] = []

    # ── 1. No activity in last 7 days ────────────────────────────────────────
    recent_updates = [
        t for t in tasks
        if t.get("updated_at") and
        (now - datetime.fromisoformat(str(t["updated_at"]).replace("Z", ""))).days <= 7
    ]
    if tasks and not recent_updates:
        anomalies.append(AnomalyAlert(
            type="inactivity",
            severity="high",
            message="No task updates in the last 7 days. This suggests the project may have stalled.",
            detected_at=now.isoformat(),
            suggested_action="Review your project plan and commit to completing at least one task today."
        ))
        health_penalties.append(25.0)

    # ── 2. All tasks created but none started ────────────────────────────────
    if len(tasks) >= 3:
        todo_count = sum(1 for t in tasks if t.get("status") == "todo")
        todo_ratio = todo_count / len(tasks)
        if todo_ratio > 0.9:
            anomalies.append(AnomalyAlert(
                type="planning_paralysis",
                severity="medium",
                message=f"{todo_count}/{len(tasks)} tasks are still in 'to-do' state. Planning without executing is a risk.",
                detected_at=now.isoformat(),
                suggested_action="Pick the most important task and start it immediately — avoid over-planning."
            ))
            health_penalties.append(15.0)

    # ── 3. Milestone missed without update ───────────────────────────────────
    for m in milestones:
        due = m.get("due_date")
        completion = m.get("completion_percentage") or 0
        if due and completion < 50:
            try:
                due_dt = datetime.strptime(str(due)[:10], "%Y-%m-%d")
                days_overdue = (now - due_dt).days
                if days_overdue > 3:
                    anomalies.append(AnomalyAlert(
                        type="overdue_milestone",
                        severity="critical" if days_overdue > 14 else "high",
                        message=f"Milestone '{m.get('title')}' is {days_overdue} days overdue at {completion}% completion.",
                        detected_at=now.isoformat(),
                        suggested_action="Inform your supervisor immediately and reschedule with a realistic new date."
                    ))
                    health_penalties.append(min(30.0, days_overdue * 1.5))
            except Exception:
                pass

    # ── 4. Suspiciously fast progress jumps ──────────────────────────────────
    progress = p.get("progress_percentage") or 0
    created = p.get("created_at")
    if created and progress > 80:
        try:
            created_dt = datetime.fromisoformat(str(created).replace("Z", ""))
            days_since_created = (now - created_dt).days
            if days_since_created < 7:
                anomalies.append(AnomalyAlert(
                    type="unrealistic_progress",
                    severity="medium",
                    message=f"Project shows {progress}% progress after only {days_since_created} days. Verify this is accurate.",
                    detected_at=now.isoformat(),
                    suggested_action="Ensure progress percentage reflects actual completed work, not estimated completion."
                ))
                health_penalties.append(10.0)
        except Exception:
            pass

    # ── 5. Too many high-priority blocked tasks ───────────────────────────────
    blocked_high = [t for t in tasks if t.get("status") == "blocked" and t.get("priority") == "high"]
    if len(blocked_high) >= 2:
        anomalies.append(AnomalyAlert(
            type="critical_blockers",
            severity="critical",
            message=f"{len(blocked_high)} high-priority tasks are blocked. This threatens core deliverables.",
            detected_at=now.isoformat(),
            suggested_action="Escalate blockers to your supervisor immediately and seek technical guidance."
        ))
        health_penalties.append(20.0)

    # ── 6. Progress stagnation ───────────────────────────────────────────────
    if progress == 0 and len(tasks) > 0:
        anomalies.append(AnomalyAlert(
            type="zero_progress",
            severity="high",
            message="Project has tasks defined but shows 0% progress. Work is not being tracked.",
            detected_at=now.isoformat(),
            suggested_action="Update your project progress percentage and mark completed tasks as done."
        ))
        health_penalties.append(20.0)

    # ── Health score ─────────────────────────────────────────────────────────
    health_score = max(0.0, 100.0 - sum(health_penalties))

    if health_score >= 80:
        status = "healthy"
    elif health_score >= 55:
        status = "warning"
    else:
        status = "critical"

    return AnomalyResponse(
        project_id=project_id,
        anomalies=anomalies,
        health_score=round(health_score, 1),
        status=status,
    )
