import json
from typing import Any, Dict, Optional

from app.services.llm_provider import ProviderChain


class DefenseQuestionAgent:
    """
    Responsible only for generating defense questions.

    Uses ProviderChain (DeepInfra -> Groq -> Ollama) so cloud providers are
    tried before falling back to the slower local Ollama model. Uses its own
    "defense" DeepInfra tier (meta-llama/Llama-3.3-70B-Instruct-Turbo) --
    moved off the "light" tier's gemma-3-12b-it, which was timing out on
    live traffic and produced lower-quality questions than this stronger
    model. Kept as its own tier rather than literally "standard" so its
    DeepInfra timeout can be tuned independently of Market Needs/DNA/Market
    Footprint, which also share "standard" (see llm_provider.py's
    _DEEPINFRA_TIER_TIMING["defense"]).
    """

    def __init__(self):
        self.provider_chain = ProviderChain(tier="defense")
        self.last_error: Optional[str] = None
        self.last_raw_response: Optional[str] = None
        self.last_provider: Optional[str] = None
        self.last_model_used: Optional[str] = None

    def generate_questions(
        self, request: Any, *, deadline: float | None = None,
    ) -> Dict[str, Any]:
        """
        ``deadline``, when supplied, is an absolute time.monotonic() cutoff
        for this Writer call -- see routers/defense_simulator.py's
        writer_deadline. Forwarded to generate_json's ``deadline``/
        ``cap_timeout_to_deadline`` kwargs, which clamp this provider
        cascade's own per-attempt timeout to whatever budget is actually
        left instead of always using the tier's full configured timeout.
        """
        self.last_error = None
        self.last_raw_response = None
        self.last_provider = None
        self.last_model_used = None

        prompt = self._build_prompt(request)

        extra_kwargs: Dict[str, Any] = {}
        if deadline is not None:
            extra_kwargs["cap_timeout_to_deadline"] = True

        try:
            result = self.provider_chain.generate_json(
                prompt, use_search=False, deadline=deadline, **extra_kwargs,
            )

            self.last_provider = (
                result.provider if result.provider != "none" else None
            )
            self.last_model_used = result.model

            if result.ok and isinstance(result.data, dict):
                self.last_raw_response = json.dumps(
                    result.data,
                    ensure_ascii=False,
                )[:3000]
                return result.data

            self.last_error = result.error or "No provider returned valid question JSON."
            return {}

        except Exception as e:
            self.last_error = str(e)
            return {}

    def _build_prompt(self, request: Any) -> str:
        profile = request.studentProfile
        idea = request.selectedIdea
        roadmap = request.roadmap or []
        se_docs = request.seDocumentation or {}
        mode = request.mode or "mixed"
        number = request.numberOfQuestions or 10
        focus_areas = getattr(request, "focusAreas", None) or []
        previous_questions = getattr(request, "previousQuestions", None) or []

        roadmap_text = "\n".join(
            [
                f"- Phase {getattr(phase, 'phaseNumber', index + 1)}: "
                f"{getattr(phase, 'name', '')} | "
                f"{getattr(phase, 'objective', '')}"
                for index, phase in enumerate(roadmap[:8])
            ]
        )

        focus_areas_text = (
            ", ".join(focus_areas)
            if focus_areas
            else "None selected -- cover a broad mix of categories."
        )

        previous_questions_text = (
            "\n".join(f"- {question}" for question in previous_questions[:20])
            if previous_questions
            else "None."
        )

        return f"""
Return ONLY valid JSON.
No markdown.
No explanation.

You are an academic final year project defense jury simulator.

Generate {number} defense questions for this project.

Defense mode: {mode}

Student-Selected Defense Fields (the student chose these focus areas for
this practice session -- questions MUST be concentrated on these fields,
not on whatever category you would pick by default):
{focus_areas_text}

Previously Asked Questions (do not repeat these or ask near-duplicates):
{previous_questions_text}

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
- Every category you use must be one of:
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
- If the student selected specific defense fields above, choose whichever of
  those categories best matches each selected field and generate most or
  all questions from that set. Do NOT default to "Problem Understanding" as
  question 1 unless the student's selected fields actually relate to it
  (e.g. "Requirements and Problem").
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