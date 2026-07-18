import json
from typing import Any, Dict, Optional

import requests


class DefenseQuestionAgent:
    """
    Responsible only for generating defense questions using Ollama.
    """

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434/api/generate",
        default_model: str = "qwen2.5-coder:7b",
    ):
        self.ollama_url = ollama_url
        self.default_model = default_model
        self.last_error: Optional[str] = None
        self.last_raw_response: Optional[str] = None

    def generate_questions(self, request: Any) -> Dict[str, Any]:
        self.last_error = None
        self.last_raw_response = None

        model = getattr(request, "model", None) or self.default_model
        prompt = self._build_prompt(request)

        try:
            return self._call_ollama_json(model=model, prompt=prompt)
        except Exception as e:
            self.last_error = str(e)
            return {}

    def _call_ollama_json(self, model: str, prompt: str) -> Dict[str, Any]:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.2,
                "num_ctx": 4096,
                "num_predict": 2200,
            },
        }

        response = requests.post(
            self.ollama_url,
            json=payload,
            timeout=300,
        )

        response.raise_for_status()

        raw = response.json().get("response", "")
        self.last_raw_response = raw[:3000]

        json_text = self._extract_json(raw)
        return json.loads(json_text)

    def _extract_json(self, text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise ValueError("No valid JSON object found in Ollama response.")

        return text[start:end + 1]

    def _build_prompt(self, request: Any) -> str:
        profile = request.studentProfile
        idea = request.selectedIdea
        roadmap = request.roadmap or []
        se_docs = request.seDocumentation or {}
        mode = request.mode or "mixed"
        number = request.numberOfQuestions or 10

        roadmap_text = "\n".join(
            [
                f"- Phase {getattr(phase, 'phaseNumber', index + 1)}: "
                f"{getattr(phase, 'name', '')} | "
                f"{getattr(phase, 'objective', '')}"
                for index, phase in enumerate(roadmap[:8])
            ]
        )

        return f"""
Return ONLY valid JSON.
No markdown.
No explanation.

You are an academic final year project defense jury simulator.

Generate {number} defense questions for this project.

Defense mode: {mode}

Student Profile:
Major: {profile.major}
Experience Level: {profile.experienceLevel}
Team Size: {profile.teamSize}
Available Hours Per Week: {profile.availableHoursPerWeek}
Skills: {profile.skills}
Skill Ratings: {profile.skillRatings}

Selected Project Idea:
Title: {idea.title}
Problem Statement: {idea.problemStatement}
Target Users: {idea.targetUsers}
Why Useful: {idea.whyUseful}
Required Technologies: {idea.requiredTechnologies}
Required Skills: {idea.requiredSkills}
Missing Skills: {idea.missingSkills}
Difficulty Level: {idea.difficultyLevel}
Expected Duration Weeks: {idea.expectedDurationWeeks}
Domain: {idea.domain}
Final Deliverables: {idea.finalDeliverables}

Roadmap:
{roadmap_text}

SE Documentation Context:
{json.dumps(se_docs, ensure_ascii=False)[:5000]}

JSON shape:
{{
  "questions": [
    {{
      "id": "DQ-01",
      "category": "Problem Understanding",
      "difficulty": "Medium",
      "question": "What problem does your project solve?",
      "expectedAnswerPoints": [
        "Point 1",
        "Point 2",
        "Point 3"
      ],
      "followUpQuestion": "A realistic follow-up question"
    }}
  ]
}}

Rules:
- Return exactly {number} questions.
- IDs must be DQ-01, DQ-02, etc.
- Categories should include:
  Problem Understanding,
  Technical Architecture,
  Database Design,
  AI Integration,
  Feasibility,
  Testing and Validation,
  Security,
  Limitations,
  Future Work,
  Business Value.
- Questions must be specific to this project, not generic.
- expectedAnswerPoints must contain 3 to 5 points.
- For strict mode, questions should be harder.
- For technical mode, focus on ASP.NET Core, FastAPI, PostgreSQL, Ollama, APIs, database, validation.
- For business mode, focus on users, value, feasibility, market, adoption.
- For friendly mode, questions should be easier and supportive.
- Do not invent implemented features.
- Do not claim the project uses ASP.NET Core Identity unless it is explicitly provided.
- Do not claim database encryption exists unless it is explicitly provided.
- Do not claim security audits were conducted.
- Do not claim deployment is complete unless it is explicitly provided.
- When discussing security, use safe wording such as authentication, authorization, ownership checks, and future security improvements.
- When discussing validation, say structured JSON validation and cleanup rules, not vague NLP validation.
- If a feature is planned but not confirmed, phrase it as future work or recommended improvement.
"""