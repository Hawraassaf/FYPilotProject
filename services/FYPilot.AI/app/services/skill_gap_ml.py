"""
ML-powered skill gap analyser with curated learning resources.
"""
import os
from openai import OpenAI
import json
from app.models.schemas import SkillGapRequest, SkillGapResponse, SkillGapItem

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY", os.getenv("AI_INTEGRATIONS_OPENAI_API_KEY", "")),
    base_url=os.getenv("AI_INTEGRATIONS_OPENAI_BASE_URL", None) or None,
)

# Curated skill database with learning resources
SKILL_RESOURCES: dict[str, list[dict]] = {
    "docker": [
        {"title": "Docker Getting Started", "url": "https://docs.docker.com/get-started/", "type": "Documentation"},
        {"title": "Docker & Kubernetes Full Course", "url": "https://www.youtube.com/watch?v=bhBSlnQcq2k", "type": "Video"},
    ],
    "kubernetes": [
        {"title": "Kubernetes Official Docs", "url": "https://kubernetes.io/docs/tutorials/", "type": "Documentation"},
    ],
    "react": [
        {"title": "React Official Tutorial", "url": "https://react.dev/learn", "type": "Documentation"},
        {"title": "Full Stack Open (React)", "url": "https://fullstackopen.com/en/", "type": "Course"},
    ],
    "python": [
        {"title": "Python for Everybody (Coursera)", "url": "https://www.coursera.org/specializations/python", "type": "Course"},
        {"title": "Real Python", "url": "https://realpython.com", "type": "Tutorial"},
    ],
    "machine learning": [
        {"title": "Andrew Ng ML Course", "url": "https://www.coursera.org/learn/machine-learning", "type": "Course"},
        {"title": "Scikit-learn User Guide", "url": "https://scikit-learn.org/stable/user_guide.html", "type": "Documentation"},
        {"title": "Fast.ai Practical Deep Learning", "url": "https://course.fast.ai/", "type": "Course"},
    ],
    "sql": [
        {"title": "SQLZoo", "url": "https://sqlzoo.net/", "type": "Tutorial"},
        {"title": "Mode SQL Tutorial", "url": "https://mode.com/sql-tutorial/", "type": "Tutorial"},
    ],
    "typescript": [
        {"title": "TypeScript Handbook", "url": "https://www.typescriptlang.org/docs/handbook/", "type": "Documentation"},
    ],
    "git": [
        {"title": "Pro Git Book (free)", "url": "https://git-scm.com/book/en/v2", "type": "Book"},
        {"title": "Learn Git Branching", "url": "https://learngitbranching.js.org/", "type": "Tutorial"},
    ],
    "fastapi": [
        {"title": "FastAPI Official Docs", "url": "https://fastapi.tiangolo.com/tutorial/", "type": "Documentation"},
    ],
    "postgresql": [
        {"title": "PostgreSQL Tutorial", "url": "https://www.postgresqltutorial.com/", "type": "Tutorial"},
    ],
    "testing": [
        {"title": "Testing with Python (pytest)", "url": "https://docs.pytest.org/", "type": "Documentation"},
        {"title": "JavaScript Testing Best Practices", "url": "https://github.com/goldbergyoni/javascript-testing-best-practices", "type": "Guide"},
    ],
    "deep learning": [
        {"title": "Deep Learning Specialisation", "url": "https://www.deeplearning.ai/courses/deep-learning-specialization/", "type": "Course"},
        {"title": "PyTorch Tutorials", "url": "https://pytorch.org/tutorials/", "type": "Documentation"},
    ],
}


def analyze_skill_gap(req: SkillGapRequest) -> SkillGapResponse:
    prompt = f"""You are a technical skills advisor for computer science students.
Analyse the skill gap between what the student knows and what the project requires.

Student's current skills: {req.current_skills}
Project technologies required: {req.project_technologies}
Target role: {req.target_role}

Return ONLY this JSON:
{{
  "gaps": [
    {{
      "skill": "skill name",
      "current_level": "none|beginner|intermediate|advanced",
      "required_level": "beginner|intermediate|advanced",
      "gap_severity": "low|medium|high|critical",
      "learning_time_weeks": <integer>,
      "resource_keys": ["key1", "key2"]
    }}
  ],
  "overall_readiness": <0-100>,
  "learning_path": ["step 1", "step 2", "step 3"],
  "estimated_weeks_to_ready": <integer>
}}

Be precise and realistic. Only include skills with actual gaps. No markdown."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)

        gaps: list[SkillGapItem] = []
        for g in data.get("gaps", []):
            skill_lower = g["skill"].lower()
            resources = []
            for key, res_list in SKILL_RESOURCES.items():
                if key in skill_lower or skill_lower in key:
                    resources.extend(res_list)
            if not resources:
                resources = [{"title": f"Search: Learn {g['skill']}", "url": f"https://www.google.com/search?q=learn+{g['skill'].replace(' ', '+')}", "type": "Search"}]
            gaps.append(SkillGapItem(
                skill=g["skill"],
                current_level=g.get("current_level", "none"),
                required_level=g.get("required_level", "intermediate"),
                gap_severity=g.get("gap_severity", "medium"),
                learning_time_weeks=g.get("learning_time_weeks", 4),
                resources=resources[:3],
            ))

        return SkillGapResponse(
            gaps=gaps,
            overall_readiness=data.get("overall_readiness", 50),
            learning_path=data.get("learning_path", []),
            estimated_weeks_to_ready=data.get("estimated_weeks_to_ready", 8),
        )
    except Exception:
        return SkillGapResponse(
            gaps=[],
            overall_readiness=50,
            learning_path=["Identify required technologies", "Start with fundamentals", "Build a small practice project"],
            estimated_weeks_to_ready=12,
        )
