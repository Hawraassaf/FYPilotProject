"""
Burndown chart and velocity analytics engine.
"""
from datetime import datetime, timedelta
from typing import Optional
import numpy as np
from app.database import execute_query
from app.models.schemas import BurndownPoint, VelocityData, BurndownResponse


def compute_burndown(project_id: int) -> BurndownResponse:
    projects = execute_query("SELECT * FROM projects WHERE id = :pid", {"pid": project_id})
    if not projects:
        raise ValueError(f"Project {project_id} not found")
    p = projects[0]

    tasks = execute_query("SELECT * FROM tasks WHERE project_id = :pid ORDER BY created_at", {"pid": project_id})

    now = datetime.utcnow()
    start_str = p.get("start_date")
    end_str = p.get("end_date")

    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d") if start_str else (now - timedelta(weeks=8))
        end_dt = datetime.strptime(end_str, "%Y-%m-%d") if end_str else (now + timedelta(weeks=8))
    except Exception:
        start_dt = now - timedelta(weeks=8)
        end_dt = now + timedelta(weeks=8)

    total_tasks = len(tasks)
    done_tasks = [t for t in tasks if t.get("status") == "done"]
    total_done = len(done_tasks)

    # Build weekly burndown
    total_days = max((end_dt - start_dt).days, 1)
    total_weeks = max(total_days // 7, 1)
    burndown: list[BurndownPoint] = []

    for week in range(total_weeks + 1):
        date = start_dt + timedelta(weeks=week)
        ideal_remaining = max(0.0, total_tasks * (1 - week / total_weeks))
        done_by_date = sum(
            1 for t in done_tasks
            if t.get("updated_at") and str(t["updated_at"])[:10] <= date.strftime("%Y-%m-%d")
        )
        actual_remaining = max(0, total_tasks - done_by_date)
        burndown.append(BurndownPoint(
            date=date.strftime("%Y-%m-%d"),
            ideal=round(ideal_remaining, 1),
            actual=float(actual_remaining),
            remaining=actual_remaining,
        ))

    # Velocity: tasks completed per week
    velocity: list[VelocityData] = []
    for week in range(min(total_weeks, 12)):
        week_start = start_dt + timedelta(weeks=week)
        week_end = week_start + timedelta(weeks=1)
        tasks_this_week = sum(
            1 for t in done_tasks
            if t.get("updated_at") and
            week_start.strftime("%Y-%m-%d") <= str(t["updated_at"])[:10] < week_end.strftime("%Y-%m-%d")
        )
        # Estimate story points: high priority = 3, medium = 2, low = 1
        pts = sum(
            (3 if t.get("priority") == "high" else 2 if t.get("priority") == "medium" else 1)
            for t in done_tasks
            if t.get("updated_at") and
            week_start.strftime("%Y-%m-%d") <= str(t["updated_at"])[:10] < week_end.strftime("%Y-%m-%d")
        )
        velocity.append(VelocityData(
            week=f"W{week + 1} ({week_start.strftime('%b %d')})",
            tasks_completed=tasks_this_week,
            story_points=float(pts),
        ))

    avg_velocity = float(np.mean([v.tasks_completed for v in velocity]) if velocity else 0)

    # Estimate completion date from velocity
    estimated_completion = None
    if avg_velocity > 0:
        remaining_tasks = total_tasks - total_done
        weeks_needed = remaining_tasks / avg_velocity
        est_dt = now + timedelta(weeks=weeks_needed)
        estimated_completion = est_dt.strftime("%Y-%m-%d")

    is_on_track = True
    if end_str:
        try:
            end_dt2 = datetime.strptime(end_str, "%Y-%m-%d")
            if estimated_completion:
                est_dt2 = datetime.strptime(estimated_completion, "%Y-%m-%d")
                is_on_track = est_dt2 <= end_dt2
        except Exception:
            pass

    return BurndownResponse(
        project_id=project_id,
        total_tasks=total_tasks,
        completed_tasks=total_done,
        burndown=burndown,
        velocity=velocity,
        avg_weekly_velocity=round(avg_velocity, 1),
        estimated_completion_date=estimated_completion,
        is_on_track=is_on_track,
    )
