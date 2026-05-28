"""
Analytics dashboard engine — productivity scores, trends, distributions.
"""
from datetime import datetime, timedelta
import numpy as np
from app.database import execute_query
from app.models.schemas import ProgressTrend, TaskDistribution, AnalyticsDashboardResponse


def get_analytics(project_id: int) -> AnalyticsDashboardResponse:
    projects = execute_query("SELECT * FROM projects WHERE id = :pid", {"pid": project_id})
    if not projects:
        raise ValueError(f"Project {project_id} not found")
    p = projects[0]

    tasks = execute_query("SELECT * FROM tasks WHERE project_id = :pid ORDER BY created_at", {"pid": project_id})
    now = datetime.utcnow()

    # ── Progress trend (last 8 weeks simulated from task completions) ─────────
    start_str = p.get("start_date")
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d") if start_str else (now - timedelta(weeks=8))
    except Exception:
        start_dt = now - timedelta(weeks=8)

    done_tasks = [t for t in tasks if t.get("status") == "done"]
    total_tasks = len(tasks)

    progress_trend: list[ProgressTrend] = []
    for week in range(9):
        date = start_dt + timedelta(weeks=week)
        if date > now:
            break
        done_by = sum(
            1 for t in done_tasks
            if t.get("updated_at") and str(t["updated_at"])[:10] <= date.strftime("%Y-%m-%d")
        )
        pct = (done_by / total_tasks * 100) if total_tasks > 0 else 0
        progress_trend.append(ProgressTrend(date=date.strftime("%Y-%m-%d"), progress=round(pct, 1)))

    # ── Task distribution ─────────────────────────────────────────────────────
    status_counts: dict[str, int] = {}
    for t in tasks:
        s = t.get("status") or "todo"
        status_counts[s] = status_counts.get(s, 0) + 1

    task_distribution = [
        TaskDistribution(
            status=s,
            count=c,
            percentage=round(c / total_tasks * 100, 1) if total_tasks > 0 else 0.0
        )
        for s, c in status_counts.items()
    ]

    # ── Completion rate ───────────────────────────────────────────────────────
    completion_rate = (len(done_tasks) / total_tasks * 100) if total_tasks > 0 else 0.0

    # ── Average task completion days ─────────────────────────────────────────
    completion_times = []
    for t in done_tasks:
        created = t.get("created_at")
        updated = t.get("updated_at")
        if created and updated:
            try:
                c_dt = datetime.fromisoformat(str(created).replace("Z", ""))
                u_dt = datetime.fromisoformat(str(updated).replace("Z", ""))
                days = (u_dt - c_dt).days
                if 0 <= days <= 365:
                    completion_times.append(days)
            except Exception:
                pass

    avg_days = float(np.mean(completion_times)) if completion_times else 0.0

    # ── Productivity score ─────────────────────────────────────────────────────
    # Combines: completion rate, velocity consistency, task diversity
    velocity_bonus = min(len(done_tasks) / 10, 1.0) * 20  # up to 20 pts for volume
    diversity_bonus = min(len(status_counts) / 4, 1.0) * 10  # up to 10 pts for using all statuses
    productivity_score = min(100.0, completion_rate * 0.7 + velocity_bonus + diversity_bonus)

    # ── Peak activity days ────────────────────────────────────────────────────
    day_counts: dict[str, int] = {}
    for t in done_tasks:
        updated = t.get("updated_at")
        if updated:
            try:
                dt = datetime.fromisoformat(str(updated).replace("Z", ""))
                day_name = dt.strftime("%A")
                day_counts[day_name] = day_counts.get(day_name, 0) + 1
            except Exception:
                pass

    peak_days = sorted(day_counts, key=day_counts.get, reverse=True)[:3]  # type: ignore[arg-type]

    return AnalyticsDashboardResponse(
        project_id=project_id,
        progress_trend=progress_trend,
        task_distribution=task_distribution,
        productivity_score=round(productivity_score, 1),
        peak_activity_days=peak_days,
        total_tasks=total_tasks,
        completion_rate=round(completion_rate, 1),
        avg_task_completion_days=round(avg_days, 1),
    )
