"""
ProjectDNAAgent — LLM-based Project DNA Analysis for FYPilot.

Design:
- ProviderChain (Groq -> Gemini -> Ollama) is used for reasoning-based project analysis.
- No dataset or trained ML model is required.
- The agent analyzes a selected project idea against the student's profile and skills.
- Python validates the LLM output and provides fallback if every provider fails.
- The app should never crash because an AI provider is unavailable.
"""

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from app.services.llm_provider import LLMResult, ProviderChain

logger = logging.getLogger("fypilot-dna-agent")


class ProjectDNARequest(BaseModel):
    ideaTitle: str
    problemStatement: str
    targetUsers: str = ""
    whyUseful: str = ""
    lebaneseMarketRelevance: str = ""
    requiredTechnologies: str = ""
    requiredSkills: str = ""
    missingSkills: str = ""
    difficultyLevel: str = "3"
    datasetNeeded: str = ""
    finalDeliverables: str = ""
    domain: str = ""
    lebaneseSector: str = ""

    studentMajor: str = "Computer Science"
    experienceLevel: str = "intermediate"
    availableHoursPerWeek: int = 10
    teamSize: int = 1
    studentSkills: list[str] = Field(default_factory=list)
    skillRatings: dict[str, int] = Field(default_factory=dict)


class DNARiskItem(BaseModel):
    title: str
    level: str
    explanation: str
    mitigation: str


class DNASkillItem(BaseModel):
    skillName: str
    status: str
    explanation: str


class ProjectDNAResponse(BaseModel):
    projectDNAType: str
    overallScore: int

    technicalFitScore: int
    skillMatchScore: int
    innovationScore: int
    feasibilityScore: int
    marketRelevanceScore: int
    dataReadinessScore: int
    scopeClarityScore: int
    supervisorFitScore: int

    riskLevel: str
    strengths: list[str]
    weaknesses: list[str]
    riskProfile: list[DNARiskItem]
    requiredSkillsAnalysis: list[DNASkillItem]
    recommendedImprovements: list[str]
    summary: str


class ProjectDNAAgent:
    """
    AI-based Project DNA Analyzer.

    The LLM performs the reasoning.
    Python validates, normalizes, and protects the application from invalid output.
    """

    def __init__(self):
        self.provider_chain = ProviderChain()

        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_provider: str | None = None
        self.last_model_used: str | None = None

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

    def analyze(self, request: ProjectDNARequest) -> ProjectDNAResponse:
        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_provider = None
        self.last_model_used = None

        prompt = self._build_prompt(request)

        raw = None

        try:
            result = self.provider_chain.generate_json(prompt, use_search=False)

            self.last_provider = (
                result.provider if result.provider != "none" else None
            )
            self.last_model_used = result.model

            if result.ok and isinstance(result.data, dict):
                self.last_raw_llm_response = json.dumps(
                    result.data,
                    ensure_ascii=False,
                )[:1500]
                raw = result.data
            else:
                self.last_error = (
                    result.error or "No provider returned valid Project DNA JSON."
                )

        except Exception as ex:
            self.last_error = f"Project DNA generation failed: {ex}"
            logger.exception("Project DNA generation failed.")

        if raw:
            self.last_llm_used = True
        else:
            self.last_llm_used = False

            if self.last_error is None:
                self.last_error = (
                    "All AI providers failed to return valid Project DNA JSON. "
                    "Used fallback DNA analysis."
                )

            raw = self._fallback_raw_analysis(request)

        return self._complete_and_validate(request, raw)

    # =========================================================================
    # Review pipeline integration (app/review/pipeline.py)
    # =========================================================================

    def build_safe_fallback(self, request: ProjectDNARequest) -> ProjectDNAResponse:
        """
        Public entry point for the deterministic fallback DNA analysis -- the
        same template-based path analyze() already falls back to internally
        when every provider fails, exposed publicly so routers never reach
        into a private method (matches ProjectRoadmapAgent.build_safe_fallback).
        """
        return self._complete_and_validate(request, self._fallback_raw_analysis(request))

    def generate_candidate(self, request: ProjectDNARequest) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Reuses analyze() end to
        end (LLM reasoning -> deterministic score clamping/completion) rather
        than duplicating it, then wraps the result as an LLMResult so it can
        flow through guarded_call like any other LLM stage.

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- when
        analyze() had to fall back internally (self.last_llm_used is False),
        since in that case there is no real candidate to review; the router
        should use build_safe_fallback() directly instead.
        """
        result = self.analyze(request)

        if not self.last_llm_used:
            return None

        return LLMResult(
            ok=True,
            provider=self.last_provider or "unknown",
            model=self.last_model_used,
            text="",
            data=result.model_dump(),
        )

    def _build_prompt(self, request: ProjectDNARequest) -> str:
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

        return f"""
You are ProjectDNAAgent inside FYPilot, an Academic Intelligence System for Final Year Project planning.

Your task is to perform an AI-based Project DNA Analysis for the selected final year project idea.

This analysis must be based only on the project idea, student profile, and student skills provided below.
Do not use external datasets.
Do not say that a dataset is required unless the project truly needs structured data.
Do not invent technologies outside the provided stack or common ASP.NET/Python/PostgreSQL stack.

Project idea:
- Title: {request.ideaTitle}
- Problem statement: {request.problemStatement}
- Target users: {request.targetUsers}
- Why useful: {request.whyUseful}
- Lebanese market relevance: {request.lebaneseMarketRelevance}
- Required technologies: {request.requiredTechnologies}
- Required skills: {request.requiredSkills}
- Missing skills: {request.missingSkills}
- Difficulty level: {request.difficultyLevel}
- Dataset needed: {request.datasetNeeded}
- Final deliverables: {request.finalDeliverables}
- Domain: {request.domain}
- Lebanese sector: {request.lebaneseSector}

Student profile:
- Major: {request.studentMajor}
- Experience level: {request.experienceLevel}
- Available hours per week: {request.availableHoursPerWeek}
- Team size: {request.teamSize}
- Student skills: {skills_text}
- Skill ratings: {ratings_text}

Rubric:
- 90 to 100 = excellent
- 75 to 89 = strong
- 60 to 74 = moderate
- 40 to 59 = weak
- below 40 = high concern

Skill rating interpretation:
- 5/5 = advanced/very strong
- 4/5 = strong
- 3/5 = intermediate and usable for MVP
- 2/5 = basic and needs improvement
- 1/5 = weak/beginner
- Do not describe a 3/5 skill as weak.
- If the student has a required skill with rating 3 or above, mark it as "Matched" or "Partial".
- Only mark a skill as "Missing" if it is not present in studentSkills.
- Only call a skill weak if its rating is 1 or 2.

Dataset interpretation:
- If datasetNeeded says "No for MVP", "No", "None", "Not required", or "Optional", do not create a major data collection risk.
- If dataset is optional, dataReadinessScore should usually be 80 or higher.
- Only create a high data risk when the project depends on sensitive, private, unavailable, or large real-world data.

Accuracy rules:
- Do not contradict the provided skill ratings.
- Do not invent weaknesses that are not supported by the project data.
- Keep risks realistic and directly connected to the input.
- Do not exaggerate risks for skills rated 3/5 or above.

Evaluate these DNA dimensions:
1. technicalFitScore: how suitable the technologies are for the project.
2. skillMatchScore: how well the student's skills match the project requirements.
3. innovationScore: how original and intelligent the project is.
4. feasibilityScore: how realistic the project is for an FYP timeline.
5. marketRelevanceScore: how useful it is for Lebanon, universities, students, SMEs, healthcare, education, or local businesses.
6. dataReadinessScore: whether required data is realistic, available, or simple to collect.
7. scopeClarityScore: whether the project scope is clear and manageable.
8. supervisorFitScore: how easy it is to supervise academically.

Risk level rules:
- Low = project is mostly realistic with minor risks.
- Medium = project is realistic but has some important risks.
- High = project has serious feasibility, dataset, skill, or scope risks.
- If riskProfile contains Medium risks, riskLevel should usually be Medium.
- If riskProfile contains High risks, riskLevel should be High.

Strict output rules:
- Return only valid JSON.
- Do not use markdown.
- Do not add explanation outside JSON.
- Do not wrap JSON in code fences.
- All scores must be integers from 0 to 100.
- riskLevel must be exactly one of: "Low", "Medium", "High".
- strengths must contain 3 short items.
- weaknesses must contain 3 short items.
- riskProfile must contain 2 to 4 risks.
- requiredSkillsAnalysis must analyze the main required skills.
- skill status must be exactly one of: "Matched", "Partial", "Missing".
- recommendedImprovements must contain 3 practical improvements.
- summary must be 2 short sentences maximum.

Return exactly this JSON structure:

{{
  "projectDNAType": "",
  "overallScore": 0,
  "technicalFitScore": 0,
  "skillMatchScore": 0,
  "innovationScore": 0,
  "feasibilityScore": 0,
  "marketRelevanceScore": 0,
  "dataReadinessScore": 0,
  "scopeClarityScore": 0,
  "supervisorFitScore": 0,
  "riskLevel": "",
  "strengths": [],
  "weaknesses": [],
  "riskProfile": [
    {{
      "title": "",
      "level": "",
      "explanation": "",
      "mitigation": ""
    }}
  ],
  "requiredSkillsAnalysis": [
    {{
      "skillName": "",
      "status": "",
      "explanation": ""
    }}
  ],
  "recommendedImprovements": [],
  "summary": ""
}}
"""

    def _complete_and_validate(
        self,
        request: ProjectDNARequest,
        raw: dict[str, Any],
    ) -> ProjectDNAResponse:
        technical = self._score(raw.get("technicalFitScore"), 70)
        skill = self._score(raw.get("skillMatchScore"), self._fallback_skill_score(request))
        innovation = self._score(raw.get("innovationScore"), 70)
        feasibility = self._score(raw.get("feasibilityScore"), 70)
        market = self._score(raw.get("marketRelevanceScore"), 75)
        data = self._score(raw.get("dataReadinessScore"), self._fallback_data_score(request))
        scope = self._score(raw.get("scopeClarityScore"), 70)
        supervisor = self._score(raw.get("supervisorFitScore"), 75)

        if self._is_dataset_optional_or_not_required(request.datasetNeeded):
            data = max(data, 82)

        if self._has_real_data_risk(request.datasetNeeded):
            data = min(data, 65)

        overall = self._score(
            raw.get("overallScore"),
            round(
                technical * 0.15
                + skill * 0.15
                + innovation * 0.12
                + feasibility * 0.18
                + market * 0.12
                + data * 0.10
                + scope * 0.10
                + supervisor * 0.08
            ),
        )

        strengths = self._list_of_strings(
            raw.get("strengths"),
            [
                "The idea has clear academic value.",
                "The project can be implemented with a realistic web-based stack.",
                "The idea can produce useful final year deliverables.",
            ],
            max_items=3,
        )

        weaknesses = self._list_of_strings(
            raw.get("weaknesses"),
            [
                "The project scope should be controlled carefully.",
                "Some technical skills may need extra practice.",
                "The MVP features should be prioritized clearly.",
            ],
            max_items=3,
        )

        risk_profile = self._risk_items(raw.get("riskProfile"), request)
        skill_analysis = self._skill_items(raw.get("requiredSkillsAnalysis"), request)

        risk_level = self._risk_level(raw.get("riskLevel"), overall)
        risk_level = self._adjust_risk_level_with_profile(risk_level, risk_profile)

        improvements = self._list_of_strings(
            raw.get("recommendedImprovements"),
            [
                "Define the MVP features before implementation.",
                "Prepare a simple database schema early.",
                "Start with core functionality before adding advanced AI features.",
            ],
            max_items=3,
        )

        summary = self._clean_text(
            raw.get("summary"),
            (
                "This project is suitable as a final year project if the scope is controlled. "
                "The student should focus on the MVP first and address missing skills early."
            ),
        )

        return ProjectDNAResponse(
            projectDNAType=self._clean_text(
                raw.get("projectDNAType"),
                self._infer_dna_type(request),
            ),
            overallScore=overall,
            technicalFitScore=technical,
            skillMatchScore=skill,
            innovationScore=innovation,
            feasibilityScore=feasibility,
            marketRelevanceScore=market,
            dataReadinessScore=data,
            scopeClarityScore=scope,
            supervisorFitScore=supervisor,
            riskLevel=risk_level,
            strengths=strengths,
            weaknesses=weaknesses,
            riskProfile=risk_profile,
            requiredSkillsAnalysis=skill_analysis,
            recommendedImprovements=improvements,
            summary=summary,
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

    def _risk_level(self, value: Any, overall_score: int) -> str:
        text = str(value or "").strip().lower()

        if text == "low":
            return "Low"

        if text == "medium":
            return "Medium"

        if text == "high":
            return "High"

        if overall_score >= 75:
            return "Low"

        if overall_score >= 55:
            return "Medium"

        return "High"

    def _adjust_risk_level_with_profile(
        self,
        risk_level: str,
        risk_profile: list[DNARiskItem],
    ) -> str:
        levels = [risk.level for risk in risk_profile]

        if "High" in levels:
            return "High"

        if "Medium" in levels:
            return "Medium"

        return risk_level

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
        max_items: int = 3,
    ) -> list[str]:
        items: list[str] = []

        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]

        for fallback_item in fallback:
            if len(items) >= max_items:
                break

            if fallback_item not in items:
                items.append(fallback_item)

        return items[:max_items]

    def _risk_items(
        self,
        value: Any,
        request: ProjectDNARequest,
    ) -> list[DNARiskItem]:
        items: list[DNARiskItem] = []

        if isinstance(value, list):
            for item in value[:4]:
                if not isinstance(item, dict):
                    continue

                items.append(
                    DNARiskItem(
                        title=self._clean_text(item.get("title"), "Project Risk"),
                        level=self._risk_level(item.get("level"), 65),
                        explanation=self._clean_text(
                            item.get("explanation"),
                            "This risk may affect project delivery.",
                        ),
                        mitigation=self._clean_text(
                            item.get("mitigation"),
                            "Control the scope and validate assumptions early.",
                        ),
                    )
                )

        if items:
            return items[:4]

        fallback = [
            DNARiskItem(
                title="Scope Control",
                level="Low",
                explanation=(
                    "The project is realistic, but the student should avoid adding too "
                    "many advanced features at once."
                ),
                mitigation="Start with a small MVP and add advanced features later.",
            ),
            DNARiskItem(
                title="MVP Prioritization",
                level="Low",
                explanation=(
                    "The student should focus on the main workflow before optional "
                    "enhancements."
                ),
                mitigation="Define must-have features and separate them from future work.",
            ),
        ]

        if self._has_real_data_risk(request.datasetNeeded):
            fallback.append(
                DNARiskItem(
                    title="Data Availability",
                    level="Medium",
                    explanation=(
                        "The project may depend on data that needs approval, collection, "
                        "or cleaning."
                    ),
                    mitigation=(
                        "Use a small realistic sample dataset or define a manual data "
                        "collection plan early."
                    ),
                )
            )

        if self._has_real_skill_gap(request.missingSkills):
            fallback.append(
                DNARiskItem(
                    title="Skill Gap",
                    level="Medium",
                    explanation=(
                        "Some required skills may need additional practice before "
                        "implementation."
                    ),
                    mitigation=(
                        "Focus on the missing skills during the first project phase."
                    ),
                )
            )

        return fallback[:4]

    def _skill_items(
        self,
        value: Any,
        request: ProjectDNARequest,
    ) -> list[DNASkillItem]:
        items: list[DNASkillItem] = []

        if isinstance(value, list):
            for item in value[:8]:
                if not isinstance(item, dict):
                    continue

                skill_name = self._clean_text(
                    item.get("skillName"),
                    "Required Skill",
                )

                status = self._normalize_skill_status(
                    item.get("status"),
                    skill_name,
                    request,
                )

                items.append(
                    DNASkillItem(
                        skillName=skill_name,
                        status=status,
                        explanation=self._clean_text(
                            item.get("explanation"),
                            self._default_skill_explanation(status),
                        ),
                    )
                )

        if items:
            return items[:8]

        required_skills = self._split_skills(request.requiredSkills)
        student_skills = [skill.lower().strip() for skill in request.studentSkills]

        for skill in required_skills[:8]:
            normalized = skill.lower()

            matched = any(
                normalized in student_skill or student_skill in normalized
                for student_skill in student_skills
            )

            status = "Matched" if matched else "Partial"

            items.append(
                DNASkillItem(
                    skillName=skill,
                    status=status,
                    explanation=self._default_skill_explanation(status),
                )
            )

        if not items:
            items.append(
                DNASkillItem(
                    skillName="Project Planning",
                    status="Partial",
                    explanation=(
                        "The required skills should be clarified before implementation."
                    ),
                )
            )

        return items[:8]

    def _normalize_skill_status(
        self,
        value: Any,
        skill_name: str,
        request: ProjectDNARequest,
    ) -> str:
        text = str(value or "").strip().lower()

        if text in ["matched", "partial", "missing"]:
            return text.capitalize()

        skill_lower = skill_name.lower()
        student_skills = [skill.lower() for skill in request.studentSkills]

        if any(skill_lower in skill or skill in skill_lower for skill in student_skills):
            return "Matched"

        rating = self._find_skill_rating(skill_name, request.skillRatings)

        if rating >= 3:
            return "Partial"

        if rating in [1, 2]:
            return "Partial"

        return "Missing"

    def _find_skill_rating(self, skill_name: str, ratings: dict[str, int]) -> int:
        skill_lower = skill_name.lower()

        for name, rating in ratings.items():
            name_lower = name.lower()

            if skill_lower in name_lower or name_lower in skill_lower:
                return int(rating)

        return 0

    def _default_skill_explanation(self, status: str) -> str:
        if status == "Matched":
            return "The student already has this skill in their profile."

        if status == "Partial":
            return "The student has a foundation but may need practice for project implementation."

        return "This skill is not clearly present in the current student profile."

    def _fallback_raw_analysis(self, request: ProjectDNARequest) -> dict[str, Any]:
        skill_score = self._fallback_skill_score(request)
        data_score = self._fallback_data_score(request)

        feasibility = 75

        if str(request.difficultyLevel).lower() in ["4", "5", "advanced", "hard"]:
            feasibility -= 10

        if request.availableHoursPerWeek < 6:
            feasibility -= 10

        if request.teamSize >= 2:
            feasibility += 5

        feasibility = max(40, min(feasibility, 90))

        market = 78 if self._mentions_local_value(request) else 65
        innovation = 78 if self._mentions_ai_or_smart(request) else 65
        technical = 78 if request.requiredTechnologies else 65
        scope = 72 if len(request.problemStatement) > 40 else 58
        supervisor = 75

        overall = round(
            technical * 0.15
            + skill_score * 0.15
            + innovation * 0.12
            + feasibility * 0.18
            + market * 0.12
            + data_score * 0.10
            + scope * 0.10
            + supervisor * 0.08
        )

        return {
            "projectDNAType": self._infer_dna_type(request),
            "overallScore": overall,
            "technicalFitScore": technical,
            "skillMatchScore": skill_score,
            "innovationScore": innovation,
            "feasibilityScore": feasibility,
            "marketRelevanceScore": market,
            "dataReadinessScore": data_score,
            "scopeClarityScore": scope,
            "supervisorFitScore": supervisor,
            "riskLevel": self._risk_level(None, overall),
            "strengths": [
                "The project has a clear final year project structure.",
                "The idea can be implemented with a realistic software stack.",
                "The project can provide useful academic deliverables.",
            ],
            "weaknesses": [
                "The project scope should be controlled carefully.",
                "Some technical skills may need extra practice.",
                "The MVP features should be prioritized clearly.",
            ],
            "riskProfile": [],
            "requiredSkillsAnalysis": [],
            "recommendedImprovements": [
                "Define the MVP features clearly.",
                "Prepare the database schema early.",
                "Implement the core workflow before adding advanced features.",
            ],
            "summary": (
                "This project is suitable if the scope is controlled. "
                "The student should focus on an MVP first and handle missing skills early."
            ),
        }

    def _fallback_skill_score(self, request: ProjectDNARequest) -> int:
        required = self._split_skills(request.requiredSkills)
        student = [skill.lower().strip() for skill in request.studentSkills]

        if not required:
            return 65

        matched = 0

        for skill in required:
            skill_lower = skill.lower()

            if any(skill_lower in current or current in skill_lower for current in student):
                matched += 1

        ratio = matched / max(len(required), 1)

        return max(35, min(round(50 + ratio * 45), 95))

    def _fallback_data_score(self, request: ProjectDNARequest) -> int:
        text = str(request.datasetNeeded or "").lower().strip()

        if not text:
            return 85

        if self._is_dataset_optional_or_not_required(text):
            return 90

        if "optional" in text:
            return 82

        if "small" in text or "manual" in text or "structured" in text:
            return 75

        if self._has_real_data_risk(text):
            return 55

        return 70

    def _split_skills(self, text: str) -> list[str]:
        if not text:
            return []

        parts = re.split(r",|;|\n", text)
        return [part.strip() for part in parts if part.strip()]

    def _is_dataset_optional_or_not_required(self, text: str) -> bool:
        lowered = str(text or "").lower().strip()

        if not lowered:
            return True

        no_data_phrases = [
            "no",
            "none",
            "no for mvp",
            "not required",
            "optional",
            "optional later",
        ]

        return any(phrase in lowered for phrase in no_data_phrases)

    def _has_real_data_risk(self, text: str) -> bool:
        lowered = str(text or "").lower().strip()

        if not lowered:
            return False

        if self._is_dataset_optional_or_not_required(lowered):
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

    def _has_real_skill_gap(self, text: str) -> bool:
        lowered = str(text or "").lower().strip()

        if not lowered:
            return False

        no_gap_phrases = [
            "none",
            "no",
            "no major",
            "no major missing skills",
            "no major missing skills for mvp",
        ]

        if any(phrase in lowered for phrase in no_gap_phrases):
            return False

        return True

    def _mentions_data(self, text: str) -> bool:
        lowered = str(text or "").lower()

        return any(
            word in lowered
            for word in ["dataset", "data", "records", "logs", "samples"]
        )

    def _mentions_ai_or_smart(self, request: ProjectDNARequest) -> bool:
        text = f"{request.ideaTitle} {request.problemStatement} {request.domain}".lower()

        return any(
            word in text
            for word in [
                "ai",
                "smart",
                "prediction",
                "recommendation",
                "intelligent",
                "analytics",
            ]
        )

    def _mentions_local_value(self, request: ProjectDNARequest) -> bool:
        text = (
            f"{request.lebaneseMarketRelevance} "
            f"{request.lebaneseSector} "
            f"{request.problemStatement}"
        ).lower()

        return any(
            word in text
            for word in [
                "lebanon",
                "lebanese",
                "local",
                "university",
                "student",
                "sme",
                "education",
                "healthcare",
            ]
        )

    def _infer_dna_type(self, request: ProjectDNARequest) -> str:
        text = f"{request.ideaTitle} {request.problemStatement} {request.domain}".lower()

        if "ai" in text or "prediction" in text or "recommendation" in text:
            return "AI-Assisted Decision Support Project"

        if (
            "market" in text
            or "e-commerce" in text
            or "business" in text
            or "sme" in text
        ):
            return "Market-Oriented Software System"

        if "student" in text or "education" in text or "university" in text:
            return "Education Technology Project"

        if "health" in text or "clinic" in text or "medical" in text:
            return "Healthcare Technology Project"

        return "Applied Software Engineering Project"