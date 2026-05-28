"""
Smart project roadmap generator.
Produces a phased, week-by-week plan based on project scope and experience level.
"""
import os
from openai import OpenAI
import json
from app.models.schemas import RoadmapRequest, RoadmapResponse, RoadmapPhase

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY", os.getenv("AI_INTEGRATIONS_OPENAI_API_KEY", "")),
    base_url=os.getenv("AI_INTEGRATIONS_OPENAI_BASE_URL", None) or None,
)


def generate_roadmap(req: RoadmapRequest) -> RoadmapResponse:
    prompt = f"""You are an expert FYP project planner for computer science students.
Generate a detailed, realistic project roadmap in JSON format.

Project Title: {req.project_title}
Description: {req.project_description}
Technologies: {req.technologies}
Experience Level: {req.experience_level}
Start Date: {req.start_date or "flexible"}
End Date: {req.end_date or "flexible"}

Return ONLY this JSON structure:
{{
  "total_weeks": <integer>,
  "phases": [
    {{
      "phase": 1,
      "name": "Research & Planning",
      "duration_weeks": <int>,
      "start_week": 1,
      "end_week": <int>,
      "deliverables": ["deliverable1", "deliverable2"],
      "tasks": ["task1", "task2", "task3"],
      "milestone": "Milestone name"
    }}
  ],
  "key_milestones": ["milestone1", "milestone2", "milestone3", "milestone4"],
  "technology_learning_order": ["tech1", "tech2"],
  "weekly_time_commitment": <hours per week>
}}

Generate 4-6 phases covering: Research, Design, Implementation, Testing, Documentation, Presentation.
Be specific to the project description and technologies. No markdown, just JSON."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        phases = [RoadmapPhase(**ph) for ph in data.get("phases", [])]
        return RoadmapResponse(
            total_weeks=data.get("total_weeks", 20),
            phases=phases,
            key_milestones=data.get("key_milestones", []),
            technology_learning_order=data.get("technology_learning_order", []),
            weekly_time_commitment=data.get("weekly_time_commitment", 20),
        )
    except Exception as e:
        # Fallback structured roadmap
        return RoadmapResponse(
            total_weeks=20,
            phases=[
                RoadmapPhase(phase=1, name="Research & Literature Review", duration_weeks=3, start_week=1, end_week=3,
                             deliverables=["Literature review document", "Problem statement"],
                             tasks=["Identify 10+ related papers", "Define problem scope", "Set up project repository"],
                             milestone="Research Complete"),
                RoadmapPhase(phase=2, name="System Design", duration_weeks=3, start_week=4, end_week=6,
                             deliverables=["Architecture diagram", "Database schema", "UI mockups"],
                             tasks=["Design system architecture", "Define API contracts", "Create wireframes", "Set up dev environment"],
                             milestone="Design Approved"),
                RoadmapPhase(phase=3, name="Core Implementation", duration_weeks=6, start_week=7, end_week=12,
                             deliverables=["Working backend", "Core frontend", "Database setup"],
                             tasks=["Implement auth", "Build core features", "Connect frontend to API", "Write unit tests"],
                             milestone="Core Features Done"),
                RoadmapPhase(phase=4, name="Advanced Features", duration_weeks=3, start_week=13, end_week=15,
                             deliverables=["Complete feature set", "Integration tests"],
                             tasks=["Implement advanced features", "Optimize performance", "Add error handling"],
                             milestone="Feature Complete"),
                RoadmapPhase(phase=5, name="Testing & Refinement", duration_weeks=2, start_week=16, end_week=17,
                             deliverables=["Test report", "Bug fixes", "Performance benchmarks"],
                             tasks=["Run full test suite", "Fix critical bugs", "User acceptance testing"],
                             milestone="Testing Complete"),
                RoadmapPhase(phase=6, name="Documentation & Submission", duration_weeks=3, start_week=18, end_week=20,
                             deliverables=["Final report", "Project presentation", "Source code"],
                             tasks=["Write final report", "Prepare demo", "Submit deliverables"],
                             milestone="Project Submitted"),
            ],
            key_milestones=["Research Complete", "Design Approved", "Core Features Done", "Testing Complete", "Project Submitted"],
            technology_learning_order=req.technologies.split(",")[:5],
            weekly_time_commitment=20,
        )
