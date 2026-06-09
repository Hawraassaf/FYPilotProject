"""
IdeaComparisonAgent — LLM-based comparison and ranking of generated project ideas for FYPilot.

Design:
- Compares all generated ideas for the current student.
- Uses student skills, team size, available hours, and experience level.
- Ollama performs reasoning and ranking.
- Python validates output and provides fallback if Ollama fails.
- Maximum compared ideas is capped to avoid slow responses.
"""

import json
import logging
import re
from typing import Any, Optional

import requests
from pydantic import BaseModel, Field

logger = logging.getLogger("fypilot-idea-comparison-agent")


class IdeaComparisonInput(BaseModel):
    id: int
    title: str
    problemStatement: str = ""
    requiredTechnologies: str = ""
    requiredSkills: str = ""
    missingSkills: str = ""
    difficultyLevel: str = "medium"
    expectedDurationWeeks: int = 10
    datasetNeeded: str = ""
    domain: str = ""
    lebaneseMarketRelevance: str = ""
    innovationScore: float = 70
    feasibilityScore: float = 70
    marketDemandScore: float = 70
    createdAt: str = ""


class IdeaComparisonRequest(BaseModel):
    studentMajor: str = "Computer Science"
    experienceLevel: str = "intermediate"
    teamSize: int = 1
    availableHoursPerWeek: int = 10
    studentSkills: list[str] = Field(default_factory=list)
    skillRatings: dict[str, int] = Field(default_factory=dict)
    ideas: list[IdeaComparisonInput] = Field(default_factory=list)


class ComparedIdeaResult(BaseModel):
    ideaId: int
    rank: int
    title: str
    overallScore: int
    skillFitScore: int
    feasibilityScore: int
    innovationScore: int
    marketRelevanceScore: int
    riskLevel: str
    bestFor: str
    strengths: list[str]
    weaknesses: list[str]
    recommendation: str


class IdeaComparisonResponse(BaseModel):
    comparisonTitle: str
    totalIdeasCompared: int
    bestIdeaId: int
    bestIdeaTitle: str
    summary: str
    ideas: list[ComparedIdeaResult]
    finalRecommendation: str


class IdeaComparisonAgent:
    """
    AI-based generated ideas comparison agent.

    Ollama ranks the ideas.
    Python validates, completes, and protects the app from invalid output.
    """

    def __init__(self, model: str = "phi3"):
        self.model = model
        self.ollama_url = "http://localhost:11434/api/generate"

        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None

        self.max_ideas = 12

        self.blocked_terms = [
            "react",
            "node",
            "vue",
            "angular",
            "flutter",
            "dart",
            "kafka",
            "azure",
            "aws",
            "gcp",
            "kubernetes",
            "blockchain",
            "web3",
            "solidity",
            "smart contract",
        ]

    def compare(self, request: IdeaComparisonRequest) -> IdeaComparisonResponse:
        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None

        ideas = self._normalize_ideas(request.ideas)

        if not ideas:
            return self._empty_response()

        limited_request = IdeaComparisonRequest(
            studentMajor=request.studentMajor,
            experienceLevel=request.experienceLevel,
            teamSize=request.teamSize,
            availableHoursPerWeek=request.availableHoursPerWeek,
            studentSkills=request.studentSkills,
            skillRatings=request.skillRatings,
            ideas=ideas,
        )

        prompt = self._build_prompt(limited_request)
        llm_text = self._call_ollama(prompt)

        raw = None

        if llm_text:
            raw = self._parse_llm_json(llm_text)

        if raw:
            self.last_llm_used = True
        else:
            self.last_llm_used = False

            if self.last_error is None:
                self.last_error = (
                    "Ollama did not return valid idea comparison JSON. "
                    "Used fallback comparison."
                )

            raw = self._fallback_raw_comparison(limited_request)

        return self._complete_and_validate(limited_request, raw)

    def _normalize_ideas(
        self,
        ideas: list[IdeaComparisonInput],
    ) -> list[IdeaComparisonInput]:
        valid_ideas = [
            idea
            for idea in ideas
            if idea.title and idea.title.strip()
        ]

        return valid_ideas[: self.max_ideas]

    def _call_ollama(self, prompt: str) -> Optional[str]:
        try:
            response = requests.post(
                self.ollama_url,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 2200,
                    },
                },
                timeout=600,
            )

            if not response.ok:
                self.last_error = f"Ollama returned HTTP status {response.status_code}"
                logger.warning(self.last_error)
                return None

            data = response.json()
            text = data.get("response")

            self.last_raw_llm_response = str(text)[:1500] if text else None

            if not text or not isinstance(text, str):
                self.last_error = "Ollama response did not contain valid text."
                return None

            return text

        except Exception as ex:
            self.last_error = str(ex)
            logger.warning("Ollama unavailable. Falling back. Error: %s", ex)
            return None

    def _build_prompt(self, request: IdeaComparisonRequest) -> str:
        skills_text = (
            ", ".join(request.studentSkills)
            if request.studentSkills
            else "No skills provided"
        )

        ratings_text = (
            ", ".join(
                f"{skill}: {rating}/5"
                for skill, rating in request.skillRatings.items()
            )
            if request.skillRatings
            else "No ratings provided"
        )

        ideas_text = json.dumps(
            [idea.model_dump() for idea in request.ideas],
            ensure_ascii=False,
            indent=2,
        )

        return f"""
You are IdeaComparisonAgent inside FYPilot, an Academic Intelligence System for Final Year Project planning.

Your task is to compare and rank generated final year project ideas for the current student.

The ideas were generated by the AI idea generation agent.
Compare ONLY the ideas provided below.
Do not invent new ideas.
Do not compare ideas from other students.
Do not recommend technologies outside the provided/common project stack.

Student profile:
- Major: {request.studentMajor}
- Experience level: {request.experienceLevel}
- Team size: {request.teamSize}
- Available hours per week: {request.availableHoursPerWeek}
- Student skills: {skills_text}
- Skill ratings: {ratings_text}

Generated ideas to compare:
{ideas_text}

Comparison criteria:
1. skillFitScore: how well the idea matches the student's skills and ratings.
2. feasibilityScore: how realistic the idea is for the team size, hours/week, and difficulty.
3. innovationScore: how creative and academically valuable the idea is.
4. marketRelevanceScore: how useful it is for Lebanon, universities, students, SMEs, healthcare, education, or local businesses.
5. riskLevel: Low, Medium, or High based on dataset risk, missing skills, scope, and difficulty.
6. overallScore: weighted final score based on the above dimensions.

Scoring guide:
- 90 to 100 = excellent
- 75 to 89 = strong
- 60 to 74 = moderate
- 40 to 59 = weak
- below 40 = not recommended

Important reasoning rules:
- A project should rank higher when it matches the student's skills, team size, and available hours.
- Do not automatically choose the most innovative idea if it is unrealistic.
- If team size is 1, prefer smaller and clearer ideas.
- If team size is 2 or more, slightly larger projects are acceptable.
- If the dataset is optional or not needed for MVP, do not create a high data risk.
- If required skills are missing or weak, reduce skillFitScore and increase risk.
- A 3/5 skill rating means usable/intermediate, not weak.
- A 4/5 or 5/5 skill rating means strong.
- Prefer ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, Bootstrap, and JavaScript when suitable.
- Do not suggest React, Node.js, Flutter, AWS, Azure, Kubernetes, blockchain, or Web3.
- Compare all provided ideas, up to the maximum sent by the application.

Strict output rules:
- Return only valid JSON.
- Do not use markdown.
- Do not add explanation outside JSON.
- Do not wrap JSON in code fences.
- ideas array must contain every idea provided in the input.
- Every idea must have a unique rank.
- Rank 1 must be the best idea.
- Scores must be integers from 0 to 100.
- riskLevel must be exactly one of: "Low", "Medium", "High".
- strengths must contain 2 short items.
- weaknesses must contain 2 short items.
- recommendation must be one short practical sentence.
- finalRecommendation must clearly explain which idea should be selected and why.

Return exactly this JSON structure:

{{
  "comparisonTitle": "Generated Ideas Comparison",
  "totalIdeasCompared": {len(request.ideas)},
  "bestIdeaId": 0,
  "bestIdeaTitle": "",
  "summary": "",
  "ideas": [
    {{
      "ideaId": 0,
      "rank": 1,
      "title": "",
      "overallScore": 0,
      "skillFitScore": 0,
      "feasibilityScore": 0,
      "innovationScore": 0,
      "marketRelevanceScore": 0,
      "riskLevel": "",
      "bestFor": "",
      "strengths": [],
      "weaknesses": [],
      "recommendation": ""
    }}
  ],
  "finalRecommendation": ""
}}
"""

    def _parse_llm_json(self, text: str) -> Optional[dict[str, Any]]:
        try:
            clean_text = text.strip()

            try:
                data = json.loads(clean_text)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", clean_text, re.DOTALL)

                if not match:
                    self.last_error = "Ollama returned text, but no JSON object was found."
                    return None

                data = json.loads(match.group(0))

            if not isinstance(data, dict):
                self.last_error = "Ollama JSON parsed, but it is not an object."
                return None

            return data

        except Exception as ex:
            self.last_error = f"Could not parse Ollama JSON: {str(ex)}"
            return None

    def _complete_and_validate(
        self,
        request: IdeaComparisonRequest,
        raw: dict[str, Any],
    ) -> IdeaComparisonResponse:
        raw_ideas = raw.get("ideas")
        completed: list[ComparedIdeaResult] = []

        for idea in request.ideas:
            raw_result = self._find_raw_result(raw_ideas, idea)
            completed.append(self._complete_idea_result(request, idea, raw_result))

        completed = sorted(
            completed,
            key=lambda item: item.overallScore,
            reverse=True,
        )

        ranked: list[ComparedIdeaResult] = []

        for index, item in enumerate(completed, start=1):
            ranked.append(
                ComparedIdeaResult(
                    ideaId=item.ideaId,
                    rank=index,
                    title=item.title,
                    overallScore=item.overallScore,
                    skillFitScore=item.skillFitScore,
                    feasibilityScore=item.feasibilityScore,
                    innovationScore=item.innovationScore,
                    marketRelevanceScore=item.marketRelevanceScore,
                    riskLevel=item.riskLevel,
                    bestFor=item.bestFor,
                    strengths=item.strengths,
                    weaknesses=item.weaknesses,
                    recommendation=item.recommendation,
                )
            )

        best = ranked[0]

        return IdeaComparisonResponse(
            comparisonTitle=self._clean_text(
                raw.get("comparisonTitle"),
                "Generated Ideas Comparison",
            ),
            totalIdeasCompared=len(ranked),
            bestIdeaId=best.ideaId,
            bestIdeaTitle=best.title,
            summary=self._clean_text(
                raw.get("summary"),
                (
                    f"{best.title} is the strongest option because it gives the best "
                    "balance between skill fit, feasibility, innovation, and market relevance."
                ),
            ),
            ideas=ranked,
            finalRecommendation=self._clean_text(
                raw.get("finalRecommendation"),
                (
                    f"Select {best.title} because it is the best overall match for "
                    "the student's skills, team size, and available development time."
                ),
            ),
        )

    def _find_raw_result(
        self,
        raw_ideas: Any,
        idea: IdeaComparisonInput,
    ) -> dict[str, Any]:
        if not isinstance(raw_ideas, list):
            return {}

        for item in raw_ideas:
            if not isinstance(item, dict):
                continue

            raw_id = self._to_int(item.get("ideaId"), -1)

            if raw_id == idea.id:
                return item

        for item in raw_ideas:
            if not isinstance(item, dict):
                continue

            raw_title = str(item.get("title") or "").strip().lower()

            if raw_title == idea.title.strip().lower():
                return item

        return {}

    def _complete_idea_result(
        self,
        request: IdeaComparisonRequest,
        idea: IdeaComparisonInput,
        raw: dict[str, Any],
    ) -> ComparedIdeaResult:
        fallback = self._fallback_idea_result(request, idea)

        skill = self._score(raw.get("skillFitScore"), fallback["skillFitScore"])
        feasibility = self._score(raw.get("feasibilityScore"), fallback["feasibilityScore"])
        innovation = self._score(raw.get("innovationScore"), fallback["innovationScore"])
        market = self._score(raw.get("marketRelevanceScore"), fallback["marketRelevanceScore"])

        overall = self._score(
            raw.get("overallScore"),
            round(
                skill * 0.30
                + feasibility * 0.30
                + innovation * 0.18
                + market * 0.22
            ),
        )

        risk = self._risk_level(raw.get("riskLevel"), overall, idea)

        return ComparedIdeaResult(
            ideaId=idea.id,
            rank=self._to_int(raw.get("rank"), 999),
            title=idea.title,
            overallScore=overall,
            skillFitScore=skill,
            feasibilityScore=feasibility,
            innovationScore=innovation,
            marketRelevanceScore=market,
            riskLevel=risk,
            bestFor=self._clean_text(raw.get("bestFor"), fallback["bestFor"]),
            strengths=self._list_of_strings(
                raw.get("strengths"),
                fallback["strengths"],
                min_items=2,
                max_items=2,
            ),
            weaknesses=self._list_of_strings(
                raw.get("weaknesses"),
                fallback["weaknesses"],
                min_items=2,
                max_items=2,
            ),
            recommendation=self._clean_text(
                raw.get("recommendation"),
                fallback["recommendation"],
            ),
        )

    def _fallback_raw_comparison(
        self,
        request: IdeaComparisonRequest,
    ) -> dict[str, Any]:
        fallback_ideas = [
            self._fallback_idea_result(request, idea)
            for idea in request.ideas
        ]

        fallback_ideas = sorted(
            fallback_ideas,
            key=lambda item: item["overallScore"],
            reverse=True,
        )

        for index, item in enumerate(fallback_ideas, start=1):
            item["rank"] = index

        best = fallback_ideas[0]

        return {
            "comparisonTitle": "Generated Ideas Comparison",
            "totalIdeasCompared": len(fallback_ideas),
            "bestIdeaId": best["ideaId"],
            "bestIdeaTitle": best["title"],
            "summary": (
                f"{best['title']} appears to be the strongest option based on "
                "skills, feasibility, market relevance, and risk level."
            ),
            "ideas": fallback_ideas,
            "finalRecommendation": (
                f"Select {best['title']} because it has the best overall balance "
                "for the current student profile."
            ),
        }

    def _fallback_idea_result(
        self,
        request: IdeaComparisonRequest,
        idea: IdeaComparisonInput,
    ) -> dict[str, Any]:
        skill = self._fallback_skill_fit_score(request, idea)
        feasibility = self._fallback_feasibility_score(request, idea)
        innovation = self._score(idea.innovationScore, 70)
        market = self._fallback_market_score(idea)

        overall = round(
            skill * 0.30
            + feasibility * 0.30
            + innovation * 0.18
            + market * 0.22
        )

        risk = self._risk_level(None, overall, idea)

        return {
            "ideaId": idea.id,
            "rank": 999,
            "title": idea.title,
            "overallScore": overall,
            "skillFitScore": skill,
            "feasibilityScore": feasibility,
            "innovationScore": innovation,
            "marketRelevanceScore": market,
            "riskLevel": risk,
            "bestFor": self._best_for_text(request, idea),
            "strengths": self._fallback_strengths(idea),
            "weaknesses": self._fallback_weaknesses(request, idea),
            "recommendation": self._fallback_recommendation(overall, idea),
        }

    def _fallback_skill_fit_score(
        self,
        request: IdeaComparisonRequest,
        idea: IdeaComparisonInput,
    ) -> int:
        required = self._split_items(idea.requiredSkills)
        student = [skill.lower().strip() for skill in request.studentSkills]

        if not required:
            return 65

        matched = 0

        for skill in required:
            skill_lower = skill.lower()

            if any(skill_lower in current or current in skill_lower for current in student):
                matched += 1

        ratio = matched / max(len(required), 1)
        score = round(50 + ratio * 45)

        if idea.missingSkills.strip():
            missing_count = len(self._split_items(idea.missingSkills))
            score -= min(missing_count * 5, 20)

        return max(35, min(score, 95))

    def _fallback_feasibility_score(
        self,
        request: IdeaComparisonRequest,
        idea: IdeaComparisonInput,
    ) -> int:
        score = self._score(idea.feasibilityScore, 70)

        difficulty = str(idea.difficultyLevel or "").lower()

        if difficulty in ["advanced", "hard", "5", "4"]:
            score -= 8

        if request.teamSize <= 1 and difficulty in ["advanced", "hard", "5", "4"]:
            score -= 8

        if request.availableHoursPerWeek < 8:
            score -= 8

        if idea.expectedDurationWeeks > 12:
            score -= 6

        if self._has_real_data_risk(idea.datasetNeeded):
            score -= 8

        return max(30, min(score, 95))

    def _fallback_market_score(self, idea: IdeaComparisonInput) -> int:
        base = self._score(idea.marketDemandScore, 70)
        text = (
            f"{idea.lebaneseMarketRelevance} "
            f"{idea.domain} "
            f"{idea.problemStatement}"
        ).lower()

        local_terms = [
            "lebanon",
            "lebanese",
            "local",
            "university",
            "student",
            "education",
            "healthcare",
            "sme",
            "business",
        ]

        if any(term in text for term in local_terms):
            base += 5

        return max(30, min(base, 100))

    def _risk_level(
        self,
        value: Any,
        overall_score: int,
        idea: IdeaComparisonInput,
    ) -> str:
        text = str(value or "").strip().lower()

        if text == "low":
            return "Low"

        if text == "medium":
            return "Medium"

        if text == "high":
            return "High"

        difficulty = str(idea.difficultyLevel or "").lower()

        if self._has_real_data_risk(idea.datasetNeeded):
            return "Medium"

        if difficulty in ["advanced", "hard", "5"] and overall_score < 75:
            return "High"

        if overall_score >= 78:
            return "Low"

        if overall_score >= 58:
            return "Medium"

        return "High"

    def _best_for_text(
        self,
        request: IdeaComparisonRequest,
        idea: IdeaComparisonInput,
    ) -> str:
        if request.teamSize <= 1:
            return "Best for a solo student who needs a focused and manageable final year project."

        return (
            f"Best for a {request.teamSize}-member team that can divide backend, UI, "
            "AI/API integration, testing, and documentation."
        )

    def _fallback_strengths(self, idea: IdeaComparisonInput) -> list[str]:
        strengths = [
            "The idea has clear academic value.",
            "The project can be implemented using a realistic software stack.",
        ]

        if idea.lebaneseMarketRelevance.strip():
            strengths[0] = "The idea has clear relevance to Lebanese students or local users."

        if "ai" in f"{idea.title} {idea.domain}".lower():
            strengths[1] = "The idea includes an intelligent AI-supported component."

        return strengths

    def _fallback_weaknesses(
        self,
        request: IdeaComparisonRequest,
        idea: IdeaComparisonInput,
    ) -> list[str]:
        weaknesses = [
            "The scope should be controlled carefully.",
            "Some technical details need to be clarified before implementation.",
        ]

        if idea.missingSkills.strip():
            weaknesses[0] = "The idea has missing skills that should be learned early."

        if self._has_real_data_risk(idea.datasetNeeded):
            weaknesses[1] = "The idea may require data that needs collection or validation."

        return weaknesses

    def _fallback_recommendation(
        self,
        overall_score: int,
        idea: IdeaComparisonInput,
    ) -> str:
        if overall_score >= 80:
            return "Strong candidate; this idea is suitable for selection."

        if overall_score >= 65:
            return "Good candidate; select it only if the student accepts the risks."

        return "Not the best option now; consider a more feasible idea."

    def _empty_response(self) -> IdeaComparisonResponse:
        return IdeaComparisonResponse(
            comparisonTitle="Generated Ideas Comparison",
            totalIdeasCompared=0,
            bestIdeaId=0,
            bestIdeaTitle="",
            summary="No generated ideas were provided for comparison.",
            ideas=[],
            finalRecommendation="Generate project ideas first, then compare them.",
        )

    def _score(self, value: Any, fallback: int) -> int:
        try:
            if value is None:
                return max(0, min(int(fallback), 100))

            if isinstance(value, str):
                value = value.replace("%", "").strip()

            score = int(round(float(value)))
            return max(0, min(score, 100))

        except Exception:
            return max(0, min(int(fallback), 100))

    def _to_int(self, value: Any, fallback: int) -> int:
        try:
            return int(value)
        except Exception:
            return fallback

    def _clean_text(self, value: Any, fallback: str) -> str:
        if value is None:
            return fallback

        if isinstance(value, list):
            text = ", ".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value).strip()

        if not text:
            return fallback

        lowered = text.lower()

        if any(blocked in lowered for blocked in self.blocked_terms):
            return fallback

        return text

    def _list_of_strings(
        self,
        value: Any,
        fallback: list[str],
        min_items: int,
        max_items: int,
    ) -> list[str]:
        items: list[str] = []

        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]

        fallback_index = 0

        while len(items) < min_items and fallback:
            fallback_item = fallback[fallback_index % len(fallback)]

            if fallback_item not in items:
                items.append(fallback_item)

            fallback_index += 1

        return items[:max_items]

    def _split_items(self, text: str) -> list[str]:
        if not text:
            return []

        parts = re.split(r",|;|\n", text)

        return [part.strip() for part in parts if part.strip()]

    def _has_real_data_risk(self, text: str) -> bool:
        lowered = str(text or "").lower().strip()

        if not lowered:
            return False

        no_data_phrases = [
            "no",
            "none",
            "no for mvp",
            "not required",
            "optional",
            "optional later",
        ]

        if any(phrase in lowered for phrase in no_data_phrases):
            return False

        high_risk_terms = [
            "sensitive",
            "private",
            "medical records",
            "patient records",
            "financial records",
            "large dataset",
            "real hospital",
            "real clinic",
            "unavailable",
            "hard to collect",
            "requires approval",
        ]

        if any(term in lowered for term in high_risk_terms):
            return True

        required_terms = [
            "required",
            "must",
            "depends on",
            "training dataset",
            "historical data",
            "real-world data",
        ]

        return any(term in lowered for term in required_terms)