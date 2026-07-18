import json
from typing import Any, Dict, Optional

import requests


class DefenseEvaluatorAgent:
    """
    Responsible only for evaluating the student's answer using Ollama.
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

    def evaluate_answer(self, request: Any) -> Dict[str, Any]:
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
                "temperature": 0.15,
                "num_ctx": 4096,
                "num_predict": 1800,
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