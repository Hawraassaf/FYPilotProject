"""
ProjectIdeaAgent — real-time, skill-based FYP idea generation for FYPilot.

Design:
- ProviderChain is the primary generation layer.
- Groq Compound Mini is used when real-time search is requested.
- Gemini and Ollama remain fallbacks through ProviderChain.
- A direct Ollama call remains as an emergency fallback.
- Scores are calculated deterministically in Python.
- Each generation returns exactly 4 ideas.
- .NET displays 2 ideas at a time and shuffles between the saved 4.
- Regenerate sends previousIdeaTitles so the agent avoids old ideas.
- The app should never crash because an AI provider is unavailable.
"""

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Any, Optional

import requests
from pydantic import BaseModel, Field

from app.services.llm_provider import ProviderChain

logger = logging.getLogger("fypilot-agent")


IDEAS_PER_BATCH = 4


# ── Pydantic models kept compatible with current ideas router ─────────────────

class StudentProfile(BaseModel):
    studentSkills: list[str]
    skillRatings: dict[str, int]
    major: str
    experienceLevel: int
    preferredDomain: str
    targetDifficulty: int
    availableHoursPerWeek: int
    teamSize: int
    projectGoals: list[str]
    lebaneseMarketRelevance: bool = True

    # Used for regenerate: Python tells Ollama to avoid these old ideas
    previousIdeaTitles: list[str] = Field(default_factory=list)
    regenerate: bool = False


class ProjectIdea(BaseModel):
    title: str
    problemStatement: str
    targetUsers: str
    whyUseful: str
    lebaneseMarketRelevance: str
    requiredTechnologies: str
    requiredSkills: str
    missingSkills: str
    difficultyLevel: int
    innovationScore: float
    feasibilityScore: float
    marketDemandScore: float
    expectedDurationWeeks: int
    supervisorCategory: str
    datasetNeeded: str
    finalDeliverables: str
    domain: str
    lebaneseSector: str


class IdeaGenerationRequest(BaseModel):
    profile: StudentProfile


class IdeaGenerationResponse(BaseModel):
    ideas: list[ProjectIdea]
    generatedAt: str
    provider: str | None = None
    modelUsed: str | None = None
    searchUsed: bool = False


# ── Agent ─────────────────────────────────────────────────────────────────────

class ProjectIdeaAgent:
    """
    Generates exactly 4 personalized FYP ideas.

    Provider priority:
    1. Groq Compound Mini with real-time search
    2. Gemini fallback
    3. Ollama fallback through ProviderChain
    4. Direct Ollama emergency fallback
    5. Deterministic fallback ideas

    Python cleans, validates, filters repetition, and calculates scores.
    """

    def __init__(self, model: str = "phi3"):
        self.provider_chain = ProviderChain()

        # Direct Ollama emergency fallback.
        self.model = model
        self.ollama_url = "http://localhost:11434/api/generate"

        # Status fields used by the router.
        self.last_llm_used = False
        self.last_error: str | None = None
        self.last_raw_llm_response: str | None = None
        self.last_provider: str | None = None
        self.last_model_used: str | None = None
        self.last_search_used = False
        self.last_search_failed = False
        self.last_search_provider: str | None = None
        self.last_search_model_used: str | None = None
        self.last_search_error: str | None = None
        self.last_sources: list[dict[str, str]] = []

        self.allowed_stack = [
            "ASP.NET Core Razor Pages",
            "Python FastAPI",
            "PostgreSQL",
            "HTML",
            "CSS",
            "Bootstrap",
            "JavaScript"
        ]

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
            "smart contract"
        ]

    def generate_ideas(self, profile: StudentProfile) -> list[ProjectIdea]:
        """
        Generate exactly four ideas using a two-step grounded workflow.

        Step 1: Groq Compound Mini performs a small dedicated web search and
        returns real source metadata.

        Step 2: The normal provider chain generates strict idea JSON from the
        student profile plus the compact evidence gathered in step 1.
        """
        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_provider = None
        self.last_model_used = None
        self.last_search_used = False
        self.last_search_failed = False
        self.last_search_provider = None
        self.last_search_model_used = None
        self.last_search_error = None
        self.last_sources = []

        raw_ideas: Optional[list[dict[str, Any]]] = None

        # --------------------------------------------------------------
        # STEP 1: Small, dedicated Groq Compound web-search request.
        # --------------------------------------------------------------
        try:
            search_result = self.provider_chain.search_web(
                self._build_search_query(profile)
            )

            self.last_search_provider = (
                search_result.provider
                if search_result.provider != "none"
                else None
            )
            self.last_search_model_used = search_result.model
            self.last_search_used = bool(
                search_result.search_used and search_result.sources
            )
            self.last_search_failed = not self.last_search_used
            self.last_search_error = search_result.error
            self.last_sources = list(search_result.sources or [])[:8]

        except Exception as ex:
            self.last_search_failed = True
            self.last_search_error = f"Search step failed: {ex}"
            logger.exception("Idea market-evidence search failed.")

        evidence_context = self._format_sources_for_prompt(self.last_sources)
        prompt = self._build_prompt(profile, evidence_context)

        # --------------------------------------------------------------
        # STEP 2: Structured generation without another web-search call.
        # --------------------------------------------------------------
        try:
            result = self.provider_chain.generate_json(
                prompt,
                use_search=False
            )

            self.last_provider = (
                result.provider
                if result.provider != "none"
                else None
            )
            self.last_model_used = result.model

            if result.ok and isinstance(result.data, dict):
                self.last_raw_llm_response = json.dumps(
                    result.data,
                    ensure_ascii=False
                )[:1500]
                raw_ideas = self._extract_ideas_from_data(result.data)
            else:
                self.last_error = (
                    result.error
                    or "ProviderChain did not return valid idea JSON."
                )

        except Exception as ex:
            self.last_error = f"Generation step failed: {ex}"
            logger.exception("ProviderChain idea generation failed.")

        # Emergency direct Ollama fallback. It receives the same evidence in
        # the prompt, so sources can still be shown when cloud generation fails.
        if not raw_ideas:
            ollama_text = self._call_ollama(prompt)

            if ollama_text:
                parsed = self._parse_llm_json(ollama_text)

                if parsed:
                    raw_ideas = parsed
                    self.last_provider = "ollama"
                    self.last_model_used = self.model

        if raw_ideas:
            self.last_llm_used = True
            self.last_error = None
        else:
            self.last_llm_used = False
            if self.last_error is None:
                self.last_error = (
                    "All AI providers returned invalid output. "
                    "Used deterministic fallback ideas."
                )
            raw_ideas = self._fallback_raw_ideas(profile)

        raw_ideas = self._remove_repeated_or_previous_ideas(
            raw_ideas,
            profile.previousIdeaTitles
        )

        if len(raw_ideas) < IDEAS_PER_BATCH:
            raw_ideas.extend(self._fallback_raw_ideas(profile))

        raw_ideas = self._remove_repeated_or_previous_ideas(
            raw_ideas,
            profile.previousIdeaTitles
        )

        backup_index = 0
        while len(raw_ideas) < IDEAS_PER_BATCH:
            raw_ideas.append(self._backup_raw_idea(profile, backup_index))
            backup_index += 1

        ideas = [
            self._complete_and_score(profile, raw)
            for raw in raw_ideas[:IDEAS_PER_BATCH]
        ]

        return ideas[:IDEAS_PER_BATCH]

    def _build_search_query(self, profile: StudentProfile) -> str:
        """Build a deliberately small Compound search request."""
        domain = str(profile.preferredDomain or "Computer Science").strip()
        major = str(profile.major or "Computer Science").strip()

        return (
            "Use live web search. Find 5 to 8 current credible sources about "
            f"real market needs, public problems, and technology opportunities "
            f"in Lebanon relevant to {domain} and {major} final-year projects. "
            "Prioritize official institutions, universities, international "
            "organizations, reputable research, and established news sources. "
            "Focus on education, SMEs, agriculture, energy, healthcare, jobs, "
            "and digital transformation when relevant. Summarize each source "
            "briefly and include its real URL."
        )

    def _format_sources_for_prompt(
        self,
        sources: list[dict[str, str]]
    ) -> str:
        """Convert real search results into a compact generation context."""
        if not sources:
            return (
                "No verified live sources were available. Avoid specific current "
                "claims, statistics, named reports, or invented citations."
            )

        lines: list[str] = []

        for index, source in enumerate(sources[:8], start=1):
            title = str(source.get("title") or "Web source").strip()[:180]
            url = str(source.get("url") or "").strip()[:500]
            snippet = " ".join(
                str(source.get("snippet") or "").split()
            )[:350]

            lines.append(
                f"{index}. {title} | {url} | {snippet}"
            )

        return "\n".join(lines)

    def _extract_ideas_from_data(
        self,
        data: dict[str, Any]
    ) -> Optional[list[dict[str, Any]]]:
        """
        Validate ProviderChain JSON without serializing and reparsing it.
        """
        ideas = data.get("ideas")

        if not isinstance(ideas, list):
            self.last_error = (
                "AI JSON was valid, but it did not contain an 'ideas' list. "
                f"Keys: {list(data.keys())}"
            )
            return None

        valid_ideas = [idea for idea in ideas if isinstance(idea, dict)]

        if not valid_ideas:
            self.last_error = "AI JSON contained an empty or invalid ideas list."
            return None

        return valid_ideas

    # ── Ollama ────────────────────────────────────────────────────────────────

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
                        "num_predict": 1400
                    }
                },
                timeout=600
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
                logger.warning(self.last_error)
                return None

            return text

        except Exception as ex:
            self.last_error = str(ex)
            logger.warning("Ollama unavailable. Falling back. Error: %s", ex)
            return None

    def _build_prompt(
        self,
        profile: StudentProfile,
        evidence_context: str = ""
    ) -> str:
        skills_text = ", ".join(
            [
                f"{skill} rating {profile.skillRatings.get(skill, 3)}/5"
                for skill in profile.studentSkills
            ]
        ) or "No skills provided"

        goals_text = (
            ", ".join(profile.projectGoals)
            if profile.projectGoals
            else "Build a useful final year project"
        )

        previous_titles = "\n".join(
            [f"- {title}" for title in profile.previousIdeaTitles if title.strip()]
        ) or "None"

        regenerate_instruction = ""
        if profile.regenerate:
            regenerate_instruction = """
This is a regeneration request.
Generate a fresh new batch of ideas.
Avoid repeating earlier ideas.
Make the concepts meaningfully different from previous generated titles.
"""

        return f"""
You are ProjectIdeaAgent inside FYPilot, an Academic Intelligence System for Final Year Project planning.

Generate exactly 4 original, current, and realistic Final Year Project ideas.

The web-search step already collected the verified evidence below. Use this
material to understand current Lebanese needs and opportunities. Do not invent
additional sources, statistics, organizations, URLs, or market claims.

VERIFIED LIVE EVIDENCE:
{evidence_context}

Every generated idea should be reasonably connected to at least one evidence
item, but do not place citations or URLs inside the idea fields. The API will
display the verified sources separately. If the evidence is weak, keep claims
qualitative. Avoid generic outdated CRUD projects. Each idea must still match
the student's skills, available time, team size, target difficulty, and domain.

Student profile:
- Major: {profile.major}
- Experience level from 0 to 5: {profile.experienceLevel}
- Preferred domain: {profile.preferredDomain}
- Target difficulty from 1 to 5: {profile.targetDifficulty}
- Available hours per week: {profile.availableHoursPerWeek}
- Team size: {profile.teamSize}
- Goals: {goals_text}
- Skills: {skills_text}

Previous generated idea titles to avoid:
{previous_titles}

{regenerate_instruction}

Strict rules:
- Ideas must be based on the student's actual skills.
- Ideas must stay inside the student's preferred domain: {profile.preferredDomain}.
- Do not randomly change the domain.
- Ideas must be realistic for a university final year project.
- Ideas should be useful for Lebanese universities, students, SMEs, healthcare, education, or local businesses.
- Do not generate the same or very similar idea to any previous title.
- Do not suggest React, Node.js, Vue, Angular, Flutter, Dart, Kafka, Azure, AWS, Kubernetes, blockchain, Web3, or Solidity.
- Use this stack when possible: ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, HTML, CSS, Bootstrap, JavaScript.
- Python FastAPI is only for AI/data science service logic.
- .NET remains responsible for authentication, Razor Pages UI, and database saving.
- Do not generate scores.
- Return only valid JSON.
- Do not use markdown.
- Do not add explanation outside JSON.
- Do not wrap the JSON in markdown code fences.
- The "ideas" array must contain exactly 4 idea objects.
- Do not leave any field empty.
- difficultyLevel must be one of: "beginner", "medium", "advanced".
- Keep every field short and direct.
- problemStatement must be one sentence only.
- whyUseful must be one sentence only.
- lebaneseMarketRelevance must be one sentence only.
- requiredTechnologies must be a comma-separated list only.
- requiredSkills must be a comma-separated list only.
- missingSkills must be a comma-separated list only.
- datasetNeeded must be one short sentence only.
- finalDeliverables must be a comma-separated list only.

The frontend will display the 4 generated ideas as 2 ideas at a time:
- First view: ideas 1 and 2
- Shuffle: ideas 3 and 4
- Shuffle again: back to ideas 1 and 2

Return exactly this JSON structure with exactly 4 filled idea objects:

{{
  "ideas": [
    {{
      "title": "",
      "problemStatement": "",
      "targetUsers": "",
      "whyUseful": "",
      "lebaneseMarketRelevance": "",
      "requiredTechnologies": "",
      "requiredSkills": "",
      "missingSkills": "",
      "difficultyLevel": "",
      "datasetNeeded": "",
      "finalDeliverables": "",
      "domain": "",
      "lebaneseSector": ""
    }}
  ]
}}
"""

    def _parse_llm_json(self, text: str) -> Optional[list[dict[str, Any]]]:
        try:
            clean_text = text.strip()

            try:
                data = json.loads(clean_text)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", clean_text, re.DOTALL)
                if not match:
                    self.last_error = "AI provider returned text, but no JSON object was found."
                    return None

                data = json.loads(match.group(0))

            ideas = data.get("ideas")

            if not isinstance(ideas, list):
                self.last_error = (
                    f"AI JSON parsed, but it does not contain an ideas list. "
                    f"Keys: {list(data.keys())}"
                )
                return None

            valid_ideas = [idea for idea in ideas if isinstance(idea, dict)]

            if len(valid_ideas) == 0:
                self.last_error = "AI JSON contains ideas, but the list is empty or invalid."
                return None

            self.last_error = None
            return valid_ideas

        except Exception as ex:
            self.last_error = f"Could not parse AI JSON: {str(ex)}"
            return None

    # ── Filtering repeated ideas ──────────────────────────────────────────────

    def _remove_repeated_or_previous_ideas(
        self,
        raw_ideas: list[dict[str, Any]],
        previous_titles: list[str]
    ) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        seen_titles: list[str] = []

        previous_normalized = [
            self._normalize_title(t)
            for t in previous_titles
            if str(t).strip()
        ]

        for idea in raw_ideas:
            title = str(idea.get("title", "")).strip()

            if not title:
                continue

            normalized = self._normalize_title(title)

            if self._is_similar_to_any(normalized, previous_normalized):
                continue

            if self._is_similar_to_any(normalized, seen_titles):
                continue

            cleaned.append(idea)
            seen_titles.append(normalized)

        return cleaned

    def _normalize_title(self, title: str) -> str:
        text = title.lower().strip()
        text = re.sub(r"[^a-z0-9 ]+", " ", text)
        text = re.sub(r"\s+", " ", text)

        removable_words = [
            "system",
            "platform",
            "application",
            "app",
            "tool",
            "solution",
            "smart",
            "ai",
            "powered",
            "assisted"
        ]

        words = [word for word in text.split() if word not in removable_words]
        return " ".join(words)

    def _is_similar_to_any(self, title: str, others: list[str]) -> bool:
        for other in others:
            if not other:
                continue

            ratio = SequenceMatcher(None, title, other).ratio()

            if ratio >= 0.72:
                return True

            title_words = set(title.split())
            other_words = set(other.split())

            if title_words and other_words:
                overlap = len(title_words & other_words) / max(
                    len(title_words),
                    len(other_words)
                )
                if overlap >= 0.7:
                    return True

        return False

    # ── Complete fields + scoring ─────────────────────────────────────────────

    def _complete_and_score(self, profile: StudentProfile, raw: dict[str, Any]) -> ProjectIdea:
        title = self._clean_text(raw.get("title"), self._default_title(profile))
        problem = self._clean_text(raw.get("problemStatement"), self._default_problem(profile))

        target_users = self._clean_text(
            raw.get("targetUsers"),
            "University students and supervisors"
        )

        why_useful = self._clean_text(
            raw.get("whyUseful"),
            "It helps students choose realistic and useful final year projects."
        )

        lebanese_relevance = self._clean_text(
            raw.get("lebaneseMarketRelevance"),
            "Useful for Lebanese universities because it supports better project planning and local problem solving."
        )

        required_technologies = self._sanitize_technologies(
            self._clean_text(raw.get("requiredTechnologies"), ", ".join(self.allowed_stack))
        )

        required_skills = self._clean_text(
            raw.get("requiredSkills"),
            self._infer_required_skills(profile, title, problem)
        )

        missing_skills = self._clean_text(
            raw.get("missingSkills"),
            self._infer_missing_skills(profile, required_skills)
        )

        difficulty = self._normalize_difficulty(
            raw.get("difficultyLevel"),
            profile.targetDifficulty
        )

        dataset_needed = self._clean_text(
            raw.get("datasetNeeded"),
            self._infer_dataset_need(profile, title, problem)
        )

        final_deliverables = self._clean_text(
            raw.get("finalDeliverables"),
            "Razor Pages web application, PostgreSQL database, Python AI service, dashboard, and final report"
        )

        domain = self._clean_text(raw.get("domain"), profile.preferredDomain)

        lebanese_sector = self._clean_text(
            raw.get("lebaneseSector"),
            self._infer_sector(domain, problem)
        )

        innovation_score = self._calculate_innovation_score(title, problem, why_useful, domain)

        feasibility_score = self._calculate_feasibility_score(
            profile,
            required_skills,
            missing_skills,
            difficulty
        )

        market_score = self._calculate_market_score(
            lebanese_relevance,
            lebanese_sector,
            problem
        )

        return ProjectIdea(
            title=title,
            problemStatement=problem,
            targetUsers=target_users,
            whyUseful=why_useful,
            lebaneseMarketRelevance=lebanese_relevance,
            requiredTechnologies=required_technologies,
            requiredSkills=required_skills,
            missingSkills=missing_skills,
            difficultyLevel=difficulty,
            innovationScore=round(innovation_score, 1),
            feasibilityScore=round(feasibility_score, 1),
            marketDemandScore=round(market_score, 1),
            expectedDurationWeeks=self._estimate_duration(profile, difficulty),
            supervisorCategory=self._supervisor_category(domain),
            datasetNeeded=dataset_needed,
            finalDeliverables=final_deliverables,
            domain=domain,
            lebaneseSector=lebanese_sector
        )

    # ── Fallback ideas ────────────────────────────────────────────────────────

    def _fallback_raw_ideas(self, profile: StudentProfile) -> list[dict[str, Any]]:
        domain = profile.preferredDomain

        fallback_templates = [
            (
                "AI-Assisted FYP Idea Recommendation Platform",
                "Students often struggle to choose final year project ideas that match their skills, timeline, and academic level.",
                "Computer science students, supervisors, and academic departments",
                "Education"
            ),
            (
                "Student Skill Gap and Learning Roadmap Generator",
                "Students may know some technologies but do not clearly understand what skills they are missing before implementation.",
                "University students and academic advisors",
                "Education"
            ),
            (
                "Lebanese Market Need Matching System for Student Projects",
                "Many final year projects are technically acceptable but weakly connected to real local market needs.",
                "Students, supervisors, SMEs, and university incubators",
                "SMEs / Education"
            ),
            (
                "Academic Progress Risk Prediction Dashboard",
                "Students and advisors often detect academic project risks too late in the semester.",
                "Students, supervisors, and academic advisors",
                "Education"
            ),
            (
                "AI-Based Supervisor Matching Assistant",
                "Students may struggle to find supervisors whose expertise matches their project idea and skills.",
                "Students, supervisors, and departments",
                "Higher Education"
            ),
            (
                "Smart Project Scope Optimizer",
                "Students often choose scopes that are too large, too vague, or unrealistic for the semester timeline.",
                "Final year students and supervisors",
                "Education"
            )
        ]

        ideas = []

        for title, problem, users, sector in fallback_templates:
            required_skills = self._infer_required_skills(profile, title, problem)

            ideas.append(
                {
                    "title": title,
                    "problemStatement": problem,
                    "targetUsers": users,
                    "whyUseful": "It gives students practical guidance and improves final year project planning quality.",
                    "lebaneseMarketRelevance": "Useful for Lebanese universities and students because it supports realistic project planning with limited resources.",
                    "requiredTechnologies": "ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, Bootstrap, JavaScript",
                    "requiredSkills": required_skills,
                    "missingSkills": self._infer_missing_skills(profile, required_skills),
                    "difficultyLevel": "medium",
                    "datasetNeeded": self._infer_dataset_need(profile, title, problem),
                    "finalDeliverables": "Razor Pages web application, PostgreSQL database, Python AI endpoint, dashboard, and final report",
                    "domain": domain,
                    "lebaneseSector": sector,
                }
            )

        return ideas

    def _backup_raw_idea(self, profile: StudentProfile, index: int) -> dict[str, Any]:
        title = f"Alternative {profile.preferredDomain} Project Concept {index + 1}"

        problem = (
            "Students need additional realistic project options that match their skills, "
            "preferred domain, and available implementation time."
        )

        required_skills = self._infer_required_skills(profile, title, problem)

        return {
            "title": title,
            "problemStatement": problem,
            "targetUsers": "Final year students and academic supervisors",
            "whyUseful": "It gives another practical project option when previous ideas are not suitable.",
            "lebaneseMarketRelevance": "Useful for Lebanese students because it supports flexible project planning under limited resources.",
            "requiredTechnologies": "ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, Bootstrap, JavaScript",
            "requiredSkills": required_skills,
            "missingSkills": self._infer_missing_skills(profile, required_skills),
            "difficultyLevel": "medium",
            "datasetNeeded": self._infer_dataset_need(profile, title, problem),
            "finalDeliverables": "Razor Pages web application, PostgreSQL database, Python AI endpoint, dashboard, and final report",
            "domain": profile.preferredDomain,
            "lebaneseSector": "Education / Digital Transformation",
        }

    # ── Cleaning / inference helpers ──────────────────────────────────────────

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

        text = text.replace(
            "Python FastAPI with PostgreSQL as backend service logic",
            "Python FastAPI for AI service logic, PostgreSQL for data storage"
        )

        text = text.replace(
            "Python FastAPI with PostgreSQL as backend service",
            "Python FastAPI for AI service logic, PostgreSQL for data storage"
        )

        text = text.replace(
            "Python FastAPI with PostgreSQL",
            "Python FastAPI for AI service logic with PostgreSQL data storage"
        )

        text = text.replace(
            "career aspirries",
            "career aspirations"
        )

        return text

    def _sanitize_technologies(self, technologies: str) -> str:
        text = technologies.strip()
        lowered = text.lower()

        if any(blocked in lowered for blocked in self.blocked_terms):
            return ", ".join(self.allowed_stack)

        return text

    def _normalize_difficulty(self, value: Any, fallback: int) -> int:
        if isinstance(value, int):
            return max(1, min(value, 5))

        text = str(value or "").lower().strip()

        mapping = {
            "easy": 1,
            "beginner": 1,
            "simple": 2,
            "medium": 3,
            "intermediate": 3,
            "hard": 4,
            "advanced": 4,
            "very hard": 5,
            "expert": 5
        }

        return mapping.get(text, max(1, min(fallback, 5)))

    def _default_title(self, profile: StudentProfile) -> str:
        return f"Smart {profile.preferredDomain} Platform for Lebanese Students"

    def _default_problem(self, profile: StudentProfile) -> str:
        return (
            f"Students need a realistic {profile.preferredDomain} solution "
            f"that matches their skills and academic timeline."
        )

    def _infer_required_skills(self, profile: StudentProfile, title: str, problem: str) -> str:
        text = f"{profile.preferredDomain} {title} {problem}".lower()

        required = ["Problem solving", "Database design"]

        if "web" in text or "platform" in text or "portal" in text or "system" in text:
            required.extend(["ASP.NET Core", "Razor Pages", "UI design"])

        if (
            "ai" in text
            or "prediction" in text
            or "recommendation" in text
            or "smart" in text
            or "data" in text
        ):
            required.extend(["Python basics", "AI logic", "data preprocessing"])

        if "market" in text or "business" in text or "sme" in text:
            required.extend(["Market analysis"])

        return ", ".join(dict.fromkeys(required))

    def _infer_missing_skills(self, profile: StudentProfile, required_skills_text: str) -> str:
        student = " ".join(profile.studentSkills).lower()
        required = required_skills_text.lower()

        missing = []

        if "asp.net" in required or "razor" in required:
            if not any(token in student for token in ["asp.net", "asp", "c#"]):
                missing.append("ASP.NET Core")

        if "database" in required or "postgres" in required or "sql" in required:
            if not any(token in student for token in ["postgres", "sql", "database"]):
                missing.append("PostgreSQL/database design")

        if "python" in required or "ai" in required or "data" in required:
            if "python" not in student:
                missing.append("Python basics")

        if "ui" in required:
            if not any(token in student for token in ["html", "css", "bootstrap", "javascript"]):
                missing.append("UI design basics")

        if "market" in required:
            if not any(token in student for token in ["market", "business"]):
                missing.append("Basic market analysis")

        if not missing:
            return "No major missing skills for MVP"

        return ", ".join(missing)

    def _infer_dataset_need(self, profile: StudentProfile, title: str, problem: str) -> str:
        text = f"{profile.preferredDomain} {title} {problem}".lower()

        if (
            "ai" in text
            or "prediction" in text
            or "recommendation" in text
            or "analytics" in text
            or "data" in text
        ):
            return "Yes. A small structured dataset is recommended."

        return "No for MVP"

    def _infer_sector(self, domain: str, problem: str) -> str:
        text = f"{domain} {problem}".lower()

        if "student" in text or "university" in text or "education" in text:
            return "Education"

        if "health" in text or "clinic" in text or "medical" in text:
            return "Healthcare"

        if "market" in text or "business" in text or "sme" in text:
            return "SMEs / Business"

        if "finance" in text or "payment" in text:
            return "Finance"

        return "Digital Transformation"

    def _supervisor_category(self, domain: str) -> str:
        text = domain.lower()

        if "ai" in text or "ml" in text or "data" in text:
            return "AI / Data Science"

        if "web" in text or "software" in text:
            return "Software Engineering"

        if "security" in text:
            return "Cybersecurity"

        return "Computer Science"

    # ── Deterministic score calculations ──────────────────────────────────────

    def _calculate_innovation_score(self, title: str, problem: str, why_useful: str, domain: str) -> float:
        text = f"{title} {problem} {why_useful} {domain}".lower()
        score = 65.0

        if "ai" in text or "smart" in text or "intelligent" in text:
            score += 8

        if "recommendation" in text or "prediction" in text or "personalized" in text:
            score += 7

        if "lebanese" in text or "lebanon" in text or "local" in text:
            score += 5

        if "dashboard" in text or "analytics" in text:
            score += 4

        return max(55.0, min(score, 95.0))

    def _calculate_feasibility_score(
        self,
        profile: StudentProfile,
        required_skills: str,
        missing_skills: str,
        difficulty: int
    ) -> float:
        ratings = list(profile.skillRatings.values())
        avg_rating = sum(ratings) / len(ratings) if ratings else 3

        score = 55.0 + (avg_rating * 6)

        if missing_skills != "No major missing skills for MVP":
            missing_count = len([x for x in missing_skills.split(",") if x.strip()])
            score -= missing_count * 5

        if difficulty <= 2:
            score += 8
        elif difficulty == 4:
            score -= 5
        elif difficulty >= 5:
            score -= 10

        if profile.availableHoursPerWeek >= 12:
            score += 8
        elif profile.availableHoursPerWeek >= 8:
            score += 4
        elif profile.availableHoursPerWeek < 5:
            score -= 10

        if profile.teamSize >= 2:
            score += 4

        return max(40.0, min(score, 95.0))

    def _calculate_market_score(self, relevance: str, sector: str, problem: str) -> float:
        text = f"{relevance} {sector} {problem}".lower()
        score = 60.0

        if "lebanon" in text or "lebanese" in text or "local" in text:
            score += 10

        if "education" in text or "student" in text or "university" in text:
            score += 8

        if "sme" in text or "business" in text or "market" in text:
            score += 6

        if "healthcare" in text or "finance" in text:
            score += 5

        return max(50.0, min(score, 95.0))

    def _estimate_duration(self, profile: StudentProfile, difficulty: int) -> int:
        weeks = 10 + (difficulty * 2)

        if profile.availableHoursPerWeek >= 12:
            weeks -= 2
        elif profile.availableHoursPerWeek < 5:
            weeks += 3

        if profile.teamSize >= 2:
            weeks -= 1

        return max(6, min(weeks, 20))