"""
ProjectRoadmapAgent — LLM-based customized project roadmap generation for FYPilot.

Design:
- Ollama generates a customized roadmap for the selected project idea.
- Python controls the number of weeks and validates the output.
- No dataset or trained ML model is required.
- If Ollama fails, a safe fallback roadmap is returned.
"""

import json
import logging
import re
from typing import Any, Optional

import requests
from pydantic import BaseModel, Field

logger = logging.getLogger("fypilot-roadmap-agent")


class ProjectRoadmapRequest(BaseModel):
    ideaTitle: str
    problemStatement: str
    requiredTechnologies: str = ""
    requiredSkills: str = ""
    missingSkills: str = ""
    difficultyLevel: str = "medium"
    expectedDurationWeeks: int = 10
    domain: str = ""
    finalDeliverables: str = ""

    teamSize: int = 1
    availableHoursPerWeek: int = 10
    studentSkills: list[str] = Field(default_factory=list)
    skillRatings: dict[str, int] = Field(default_factory=dict)


class RoadmapWeek(BaseModel):
    weekNumber: int
    phaseTitle: str
    mainGoal: str
    tasks: list[str]
    deliverables: list[str]
    teamResponsibilities: list[str]
    skillsToLearn: list[str]
    riskWarning: str
    checkpoint: str


class ProjectRoadmapResponse(BaseModel):
    roadmapTitle: str
    totalWeeks: int
    difficultyLevel: str
    teamStrategy: str
    weeks: list[RoadmapWeek]
    finalAdvice: str


class ProjectRoadmapAgent:
    """
    AI-based Project Roadmap Generator.

    Code controls structure.
    Ollama customizes the roadmap.
    Python validates the response.
    """

    def __init__(self, model: str = "phi3"):
        self.model = model
        self.ollama_url = "http://localhost:11434/api/generate"

        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None

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
            "flask",
            "balsamiq",
            "figma",
        ]

    def generate(self, request: ProjectRoadmapRequest) -> ProjectRoadmapResponse:
        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None

        total_weeks = self._normalize_weeks(request.expectedDurationWeeks)

        prompt = self._build_prompt(request, total_weeks)
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
                    "Ollama did not return valid roadmap JSON. "
                    "Used fallback roadmap."
                )

            raw = self._fallback_raw_roadmap(request, total_weeks)

        return self._complete_and_validate(request, raw, total_weeks)

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
                        "num_predict": 2400,
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

    def _build_prompt(self, request: ProjectRoadmapRequest, total_weeks: int) -> str:
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

        fixed_structure = self._base_week_structure(total_weeks)

        return f"""
You are ProjectRoadmapAgent inside FYPilot, an Academic Intelligence System for Final Year Project planning.

Generate a customized weekly implementation roadmap for the selected final year project.

Project idea:
- Title: {request.ideaTitle}
- Problem statement: {request.problemStatement}
- Domain: {request.domain}
- Required technologies: {request.requiredTechnologies}
- Required skills: {request.requiredSkills}
- Missing skills: {request.missingSkills}
- Difficulty level: {request.difficultyLevel}
- Final deliverables: {request.finalDeliverables}

Student/team:
- Team size: {request.teamSize}
- Available hours per week: {request.availableHoursPerWeek}
- Student skills: {skills_text}
- Skill ratings: {ratings_text}

Use this fixed week structure:
{fixed_structure}

Strict rules:
- Return only valid JSON.
- Do not use markdown.
- Do not add explanation outside JSON.
- Do not wrap JSON in code fences.
- Generate exactly {total_weeks} weeks.
- Do not skip week numbers.
- Do not change the week numbers.
- Each week must be customized to the selected project.
- Keep tasks realistic for the team size and available hours.
- If team size is 1, teamResponsibilities should mention "Student".
- If team size is more than 1, split responsibilities between members.
- Use "Member 1", "Member 2", etc. Do not use "Student 1" or "Student 2".
- teamResponsibilities must contain exactly one item per team member.
- Each team responsibility must be specific to that week’s phase.
- Do not repeat the same responsibility sentence across weeks.
- Mention missing skills in early weeks as learning tasks.
- Do not suggest React, Node.js, Flutter, AWS, Azure, Kubernetes, blockchain, Web3, Flask, Balsamiq, or Figma.
- Prefer ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, Bootstrap, JavaScript.
- Never mention Flask. Use Python FastAPI only.
- Never mention external design tools unless they are provided in the input.
- For UI weeks, assign UI/design/documentation tasks.
- For database weeks, assign schema/database tasks.
- For AI/API weeks, assign FastAPI/Ollama/API integration tasks.
- For testing weeks, assign testing/debugging tasks.
- For documentation weeks, assign report/user-guide/presentation tasks.
- Do not mix too many fallback-style tasks with AI-customized tasks.
- Avoid repeating the same task in multiple weeks.
- Each week must include 3 to 5 tasks.
- Each week must include 1 to 3 deliverables.
- Each week must include 1 to 4 skillsToLearn.
- riskWarning must be one short sentence.
- checkpoint must be one short sentence.

Return exactly this JSON structure:

{{
  "roadmapTitle": "",
  "totalWeeks": {total_weeks},
  "difficultyLevel": "",
  "teamStrategy": "",
  "weeks": [
    {{
      "weekNumber": 1,
      "phaseTitle": "",
      "mainGoal": "",
      "tasks": [],
      "deliverables": [],
      "teamResponsibilities": [],
      "skillsToLearn": [],
      "riskWarning": "",
      "checkpoint": ""
    }}
  ],
  "finalAdvice": ""
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
        request: ProjectRoadmapRequest,
        raw: dict[str, Any],
        total_weeks: int,
    ) -> ProjectRoadmapResponse:
        raw_weeks = raw.get("weeks")
        fallback_weeks = self._fallback_raw_roadmap(request, total_weeks)["weeks"]

        completed_weeks: list[RoadmapWeek] = []

        for index in range(total_weeks):
            raw_week = {}

            if isinstance(raw_weeks, list) and index < len(raw_weeks):
                if isinstance(raw_weeks[index], dict):
                    raw_week = raw_weeks[index]

            if not raw_week:
                raw_week = fallback_weeks[index]

            completed_weeks.append(
                self._complete_week(request, raw_week, index + 1)
            )

        return ProjectRoadmapResponse(
            roadmapTitle=self._clean_text(
                raw.get("roadmapTitle"),
                f"Implementation Roadmap for {request.ideaTitle}",
            ),
            totalWeeks=total_weeks,
            difficultyLevel=self._clean_text(
                raw.get("difficultyLevel"),
                request.difficultyLevel,
            ),
            teamStrategy=self._clean_text(
                raw.get("teamStrategy"),
                self._default_team_strategy(request),
            ),
            weeks=completed_weeks,
            finalAdvice=self._clean_text(
                raw.get("finalAdvice"),
                (
                    "Start with the MVP, review progress weekly, and avoid adding "
                    "advanced features before the core workflow is stable."
                ),
            ),
        )

    def _complete_week(
        self,
        request: ProjectRoadmapRequest,
        raw_week: dict[str, Any],
        week_number: int,
    ) -> RoadmapWeek:
        fallback = self._fallback_week(request, week_number)
        team_count = max(1, min(request.teamSize, 5))

        return RoadmapWeek(
            weekNumber=week_number,
            phaseTitle=self._clean_text(
                raw_week.get("phaseTitle"),
                fallback["phaseTitle"],
            ),
            mainGoal=self._clean_text(
                raw_week.get("mainGoal"),
                fallback["mainGoal"],
            ),
            tasks=self._list_of_strings(
                raw_week.get("tasks"),
                fallback["tasks"],
                min_items=3,
                max_items=5,
            ),
            deliverables=self._list_of_strings(
                raw_week.get("deliverables"),
                fallback["deliverables"],
                min_items=1,
                max_items=3,
            ),
            teamResponsibilities=self._list_of_strings(
                raw_week.get("teamResponsibilities"),
                fallback["teamResponsibilities"],
                min_items=team_count,
                max_items=team_count,
            ),
            skillsToLearn=self._list_of_strings(
                raw_week.get("skillsToLearn"),
                fallback["skillsToLearn"],
                min_items=1,
                max_items=4,
            ),
            riskWarning=self._clean_text(
                raw_week.get("riskWarning"),
                fallback["riskWarning"],
            ),
            checkpoint=self._clean_text(
                raw_week.get("checkpoint"),
                fallback["checkpoint"],
            ),
        )

    def _fallback_raw_roadmap(
        self,
        request: ProjectRoadmapRequest,
        total_weeks: int,
    ) -> dict[str, Any]:
        return {
            "roadmapTitle": f"Implementation Roadmap for {request.ideaTitle}",
            "totalWeeks": total_weeks,
            "difficultyLevel": request.difficultyLevel,
            "teamStrategy": self._default_team_strategy(request),
            "weeks": [
                self._fallback_week(request, week_number)
                for week_number in range(1, total_weeks + 1)
            ],
            "finalAdvice": (
                "Focus on the MVP first, then add AI or advanced features only "
                "after the core system works."
            ),
        }

    def _fallback_week(
        self,
        request: ProjectRoadmapRequest,
        week_number: int,
    ) -> dict[str, Any]:
        phases = self._phase_names(self._normalize_weeks(request.expectedDurationWeeks))
        phase = phases[min(week_number - 1, len(phases) - 1)]

        return {
            "weekNumber": week_number,
            "phaseTitle": phase,
            "mainGoal": f"Complete the {phase.lower()} phase for {request.ideaTitle}.",
            "tasks": self._default_tasks_for_phase(phase),
            "deliverables": self._default_deliverables_for_phase(phase),
            "teamResponsibilities": self._default_responsibilities(request, phase),
            "skillsToLearn": self._default_skills_to_learn(request, week_number),
            "riskWarning": "Avoid expanding the scope beyond the MVP.",
            "checkpoint": f"Supervisor reviews the {phase.lower()} progress.",
        }

    def _normalize_weeks(self, weeks: int) -> int:
        try:
            value = int(weeks)
        except Exception:
            value = 10

        return max(6, min(value, 12))

    def _base_week_structure(self, total_weeks: int) -> str:
        phases = self._phase_names(total_weeks)

        return "\n".join(
            f"Week {index + 1}: {phase}"
            for index, phase in enumerate(phases)
        )

    def _phase_names(self, total_weeks: int) -> list[str]:
        base = [
            "Requirements and Scope Definition",
            "UI/UX Design and User Flow",
            "Database Design and Architecture",
            "Authentication and Core Setup",
            "Core Feature Development",
            "AI/API Service Integration",
            "Dashboard and Reports",
            "Testing and Bug Fixing",
            "Documentation and User Guide",
            "Final Demo and Presentation Preparation",
            "Supervisor Feedback Improvements",
            "Final Deployment and Submission",
        ]

        return base[:total_weeks]

    def _default_tasks_for_phase(self, phase: str) -> list[str]:
        mapping = {
            "Requirements and Scope Definition": [
                "Refine the final problem statement.",
                "Define target users and MVP features.",
                "Confirm project boundaries with the supervisor.",
                "Write functional requirements.",
            ],
            "UI/UX Design and User Flow": [
                "Design main page layouts.",
                "Create the navigation flow.",
                "Prepare simple wireframes.",
                "Review user experience with the team.",
            ],
            "Database Design and Architecture": [
                "Identify main entities.",
                "Create database tables and relationships.",
                "Plan how project data will be stored.",
                "Review database schema with the supervisor.",
            ],
            "Authentication and Core Setup": [
                "Set up the project structure.",
                "Implement login and user roles.",
                "Prepare the main dashboard layout.",
                "Connect the database to the web app.",
            ],
            "Core Feature Development": [
                "Implement the main project workflow.",
                "Connect forms to the database.",
                "Validate user inputs.",
                "Test the core feature manually.",
            ],
            "AI/API Service Integration": [
                "Create or connect the Python FastAPI endpoint.",
                "Send selected project data to the AI service.",
                "Display structured AI results in the web app.",
                "Handle fallback if the AI service is unavailable.",
            ],
            "Dashboard and Reports": [
                "Create summary cards.",
                "Display roadmap and analysis results.",
                "Improve readability of the UI.",
                "Add progress indicators.",
            ],
            "Testing and Bug Fixing": [
                "Test all main user flows.",
                "Fix validation and database issues.",
                "Check edge cases.",
                "Improve error messages.",
            ],
            "Documentation and User Guide": [
                "Write technical documentation.",
                "Prepare screenshots.",
                "Explain system features clearly.",
                "Write setup and run instructions.",
            ],
            "Final Demo and Presentation Preparation": [
                "Prepare the demo scenario.",
                "Practice explaining the AI workflow.",
                "Finalize presentation points.",
                "Test the full project before the demo.",
            ],
            "Supervisor Feedback Improvements": [
                "Apply supervisor feedback.",
                "Polish UI and reports.",
                "Fix remaining issues.",
                "Improve final deliverables.",
            ],
            "Final Deployment and Submission": [
                "Prepare final project package.",
                "Check database and service configuration.",
                "Prepare final submission files.",
                "Run the final demo test.",
            ],
        }

        return mapping.get(
            phase,
            [
                "Work on the planned project phase.",
                "Review progress with the team.",
                "Prepare a deliverable for this week.",
            ],
        )

    def _default_deliverables_for_phase(self, phase: str) -> list[str]:
        mapping = {
            "Requirements and Scope Definition": [
                "Requirements document",
                "MVP feature list",
            ],
            "UI/UX Design and User Flow": [
                "Wireframes",
                "User flow diagram",
            ],
            "Database Design and Architecture": [
                "Database schema",
                "Entity relationship plan",
            ],
            "Authentication and Core Setup": [
                "Authentication pages",
                "Initial dashboard",
            ],
            "Core Feature Development": [
                "Working core feature",
                "Database-connected forms",
            ],
            "AI/API Service Integration": [
                "Connected AI endpoint",
                "Displayed AI results",
            ],
            "Dashboard and Reports": [
                "Dashboard cards",
                "Report section",
            ],
            "Testing and Bug Fixing": [
                "Tested workflows",
                "Bug fix list",
            ],
            "Documentation and User Guide": [
                "Technical documentation",
                "User guide",
            ],
            "Final Demo and Presentation Preparation": [
                "Demo script",
                "Presentation outline",
            ],
            "Supervisor Feedback Improvements": [
                "Improved UI",
                "Updated features",
            ],
            "Final Deployment and Submission": [
                "Final project package",
                "Submission checklist",
            ],
        }

        return mapping.get(
            phase,
            [
                f"{phase} output",
                "Updated project progress",
            ],
        )

    def _default_responsibilities(
        self,
        request: ProjectRoadmapRequest,
        phase: str,
    ) -> list[str]:
        if request.teamSize <= 1:
            return [f"Student: complete the {phase.lower()} tasks and report progress."]

        phase_lower = phase.lower()

        if "requirements" in phase_lower or "scope" in phase_lower:
            responsibilities = [
                "Member 1: define requirements and MVP scope.",
                "Member 2: collect user needs and prepare documentation.",
                "Member 3: review feasibility and technical constraints.",
                "Member 4: validate project assumptions and constraints.",
                "Member 5: organize supervisor feedback notes.",
            ]
        elif "ui" in phase_lower or "user flow" in phase_lower:
            responsibilities = [
                "Member 1: review UI flow with backend requirements.",
                "Member 2: design layouts and page structure.",
                "Member 3: prepare usability notes and testing checklist.",
                "Member 4: review accessibility and navigation.",
                "Member 5: document UI decisions.",
            ]
        elif "database" in phase_lower:
            responsibilities = [
                "Member 1: design database entities and relationships.",
                "Member 2: review forms and required stored data.",
                "Member 3: prepare sample records and test cases.",
                "Member 4: check database constraints and indexes.",
                "Member 5: document schema decisions.",
            ]
        elif "authentication" in phase_lower:
            responsibilities = [
                "Member 1: implement authentication and roles.",
                "Member 2: test login, logout, and access flow.",
                "Member 3: document security behavior.",
                "Member 4: review authorization rules.",
                "Member 5: prepare test accounts.",
            ]
        elif "core feature" in phase_lower:
            responsibilities = [
                "Member 1: implement backend logic for the core workflow.",
                "Member 2: build the UI for the main workflow.",
                "Member 3: test database-connected features.",
                "Member 4: write validation scenarios.",
                "Member 5: document feature behavior.",
            ]
        elif "ai" in phase_lower or "api" in phase_lower:
            responsibilities = [
                "Member 1: connect .NET to the Python FastAPI endpoint.",
                "Member 2: test API responses in the UI.",
                "Member 3: validate AI output and fallback behavior.",
                "Member 4: document API request and response fields.",
                "Member 5: test error handling when AI is unavailable.",
            ]
        elif "dashboard" in phase_lower or "reports" in phase_lower:
            responsibilities = [
                "Member 1: prepare dashboard data from the backend.",
                "Member 2: design dashboard cards and layout.",
                "Member 3: test displayed summaries and reports.",
                "Member 4: improve visual clarity.",
                "Member 5: document dashboard behavior.",
            ]
        elif "testing" in phase_lower:
            responsibilities = [
                "Member 1: test backend and database flows.",
                "Member 2: test UI and user experience.",
                "Member 3: document bugs and verify fixes.",
                "Member 4: test edge cases.",
                "Member 5: prepare final testing notes.",
            ]
        elif "documentation" in phase_lower:
            responsibilities = [
                "Member 1: write technical implementation details.",
                "Member 2: prepare screenshots and user guide.",
                "Member 3: review formatting and completeness.",
                "Member 4: write setup instructions.",
                "Member 5: prepare feature descriptions.",
            ]
        elif "presentation" in phase_lower or "demo" in phase_lower:
            responsibilities = [
                "Member 1: explain backend and database workflow.",
                "Member 2: explain UI and user flow.",
                "Member 3: explain AI/API integration.",
                "Member 4: prepare demo scenario.",
                "Member 5: practice expected questions.",
            ]
        elif "feedback" in phase_lower:
            responsibilities = [
                "Member 1: apply backend-related feedback.",
                "Member 2: polish UI based on feedback.",
                "Member 3: verify fixed issues.",
                "Member 4: update documentation.",
                "Member 5: prepare final review notes.",
            ]
        elif "deployment" in phase_lower or "submission" in phase_lower:
            responsibilities = [
                "Member 1: verify backend configuration.",
                "Member 2: check UI and navigation.",
                "Member 3: test AI service startup.",
                "Member 4: prepare submission files.",
                "Member 5: run final demo test.",
            ]
        else:
            responsibilities = [
                "Member 1: handle backend-related tasks.",
                "Member 2: handle UI and documentation tasks.",
                "Member 3: handle testing and support tasks.",
                "Member 4: review project quality.",
                "Member 5: prepare progress notes.",
            ]

        return responsibilities[: request.teamSize]

    def _default_skills_to_learn(
        self,
        request: ProjectRoadmapRequest,
        week_number: int,
    ) -> list[str]:
        missing = self._split_items(request.missingSkills)

        if week_number <= 3 and missing:
            return missing[:3]

        if week_number <= 3:
            return ["requirements analysis", "database planning"]

        if week_number <= 6:
            return ["ASP.NET Core implementation", "PostgreSQL integration"]

        return ["testing", "documentation", "presentation preparation"]

    def _default_team_strategy(self, request: ProjectRoadmapRequest) -> str:
        if request.teamSize <= 1:
            return (
                "Solo strategy: keep the MVP small, finish core features first, "
                "and avoid unnecessary advanced modules."
            )

        return (
            f"Team strategy: divide work across {request.teamSize} members, "
            "with clear ownership for backend, UI, AI/API integration, testing, "
            "and documentation."
        )

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