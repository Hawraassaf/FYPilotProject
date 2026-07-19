import json
from typing import Any, Dict, Optional

from app.services.llm_provider import ProviderChain


class DefenseEvaluatorAgent:
    """
    Responsible only for evaluating the student's answer.

    Uses ProviderChain (Groq -> Gemini -> Ollama) so cloud providers are tried
    before falling back to the slower local Ollama model.
    """

    def __init__(self):
        self.provider_chain = ProviderChain()
        self.last_error: Optional[str] = None
        self.last_raw_response: Optional[str] = None
        self.last_provider: Optional[str] = None
        self.last_model_used: Optional[str] = None

    def evaluate_answer(self, request: Any) -> Dict[str, Any]:
        self.last_error = None
        self.last_raw_response = None
        self.last_provider = None
        self.last_model_used = None

        prompt = self._build_prompt(request)

        try:
            result = self.provider_chain.generate_json(prompt, use_search=False)

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

            self.last_error = result.error or "No provider returned valid evaluation JSON."
            return {}

        except Exception as e:
            self.last_error = str(e)
            return {}

    def _build_prompt(self, request: Any) -> str:
        question = request.question
        student_answer = request.studentAnswer
        profile = request.studentProfile
        idea = request.selectedIdea
        mode = request.mode or "mixed"

        return f"""
Return ONLY valid JSON.
No markdown.
No explanation.

You are an academic final year project defense examiner.

Evaluate the student's defense answer.

Defense mode: {mode}

Student Profile:
{profile.model_dump() if profile else {}}

Selected Idea:
{idea.model_dump() if idea else {}}

Question:
{question.model_dump() if hasattr(question, "model_dump") else question}

Student Answer:
{student_answer}

Expected Answer Points:
{question.expectedAnswerPoints if hasattr(question, "expectedAnswerPoints") else []}

Grading rules:
- Score from 0 to 100.
- Be fair but academic.
- Give clear strengths.
- Give missing points only if the student's answer truly did not cover them.
- Do not mark a point as missing if the student mentioned it using similar wording.
- Accept paraphrased answers, not only exact wording.
- If the student gives examples, recognize them as examples.
- Give a better answer the student can memorize.
- Ask one follow-up question.
- Do not be rude.
- Do not give a score above 90 unless the answer is excellent and complete.
- Do not invent project features that were not provided in the selected idea.
- Do not claim security, deployment, encryption, or identity features unless they are clearly present in the provided context.
JSON shape:
{{
  "score": 78,
  "level": "Good",
  "strengths": [
    "Strength 1"
  ],
  "missingPoints": [
    "Missing point 1"
  ],
  "improvedAnswer": "A stronger answer would be...",
  "followUpQuestion": "Follow-up question",
  "feedbackSummary": "Short feedback summary"
}}
"""