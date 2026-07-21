"""
ProjectRoadmapAgent — LLM-based customized project roadmap generation for FYPilot.

Design (v2):
- The LLM designs 4-7 PHASES tailored to the specific idea, each with a
  flexible duration (1-4 weeks) and concrete, idea-specific tasks.
- Python then does everything structural deterministically:
    * normalizes phase durations so they sum exactly to expectedDurationWeeks
      (largest-remainder scaling, minimum 1 week per phase)
    * expands phases into week entries (3-5 tasks per week)
    * derives per-member responsibilities FROM that week's actual tasks,
      guaranteeing exactly teamSize items, specific and non-repeating
    * places deliverables and supervisor checkpoints on phase-final weeks
    * front-loads missing skills as learning items in the first weeks
- The public request/response contract is UNCHANGED: .NET still sends
  ProjectRoadmapRequest and receives week-based ProjectRoadmapResponse.
- One small LLM call (~1300 output tokens) instead of one giant week-by-week
  generation: no truncation, faster on CPU-only Ollama.
- If Ollama fails, the previous safe fallback roadmap is returned.
"""

import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.services.llm_provider import LLMResult, ProviderChain

logger = logging.getLogger("fypilot-roadmap-agent")


# =============================================================================
# Public contract (UNCHANGED — .NET depends on these shapes)
# =============================================================================


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


# =============================================================================
# Internal phase-plan models (what the LLM actually generates)
# =============================================================================


class _PhasePlan(BaseModel):
    name: str
    weeks: int = 1
    goal: str = ""
    tasks: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    skillsToLearn: list[str] = Field(default_factory=list)
    riskWarning: str = ""
    checkpoint: str = ""


class _RoadmapPlan(BaseModel):
    roadmapTitle: str = ""
    teamStrategy: str = ""
    finalAdvice: str = ""
    phases: list[_PhasePlan] = Field(default_factory=list)


# =============================================================================
# Agent
# =============================================================================


class ProjectRoadmapAgent:
    """
    AI-based Project Roadmap Generator.

    The LLM customizes phase design; Python owns structure, durations,
    weekly expansion, and team responsibility derivation.
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
            "node.js",
            "nodejs",
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

        self.generic_task_phrases = [
            "develop core features",
            "work on the project",
            "continue development",
            "implement features",
            "do the tasks",
            "complete the phase",
            "work on the planned",
        ]

    # =========================================================================
    # Main entry point
    # =========================================================================

    def generate(self, request: ProjectRoadmapRequest) -> ProjectRoadmapResponse:
        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_provider = None
        self.last_model_used = None

        total_weeks = self._normalize_weeks(request.expectedDurationWeeks)

        plan = self._try_generate_phase_plan(request, total_weeks)

        if plan is not None and plan.phases:
            self.last_llm_used = True
            return self._expand_plan_to_weeks(request, plan, total_weeks)

        self.last_llm_used = False

        if self.last_error is None:
            self.last_error = (
                "No AI provider returned a valid roadmap phase plan. "
                "Used fallback roadmap."
            )

        raw = self._fallback_raw_roadmap(request, total_weeks)
        return self._complete_and_validate(request, raw, total_weeks)

    # =========================================================================
    # Review pipeline integration (app/review/pipeline.py)
    # =========================================================================

    def build_safe_fallback(self, request: ProjectRoadmapRequest) -> ProjectRoadmapResponse:
        """
        Public entry point for the deterministic fallback roadmap -- the same
        template-based path generate() already falls back to internally when
        every provider fails, exposed publicly so routers never reach into a
        private method (matches FypMentorAgent.build_safe_fallback).
        """
        total_weeks = self._normalize_weeks(request.expectedDurationWeeks)
        raw = self._fallback_raw_roadmap(request, total_weeks)
        return self._complete_and_validate(request, raw, total_weeks)

    def generate_candidate(self, request: ProjectRoadmapRequest) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Reuses generate() end to
        end (LLM phase design -> deterministic week expansion) rather than
        duplicating it, then wraps the result as an LLMResult so it can flow
        through guarded_call like any other LLM stage.

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- when
        generate() itself had to fall back internally (self.last_llm_used is
        False), since in that case there is no real candidate to review; the
        router should use build_safe_fallback() directly instead.
        """
        result = self.generate(request)

        if not self.last_llm_used:
            return None

        return LLMResult(
            ok=True,
            provider=self.last_provider or "unknown",
            model=self.last_model_used,
            text="",
            data=result.model_dump(),
        )

    # =========================================================================
    # LLM phase-plan generation
    # =========================================================================

    def _try_generate_phase_plan(
        self,
        request: ProjectRoadmapRequest,
        total_weeks: int,
    ) -> Optional[_RoadmapPlan]:
        prompt = self._build_phase_prompt(request, total_weeks)

        try:
            result = self.provider_chain.generate_json(prompt, use_search=False)
        except Exception as ex:
            self.last_error = f"Roadmap generation failed: {ex}"
            logger.exception("Roadmap generation failed.")
            return None

        self.last_provider = (
            result.provider if result.provider != "none" else None
        )
        self.last_model_used = result.model

        if not result.ok or not isinstance(result.data, dict):
            self.last_error = result.error or "No provider returned valid roadmap JSON."
            return None

        self.last_raw_llm_response = json.dumps(
            result.data,
            ensure_ascii=False,
        )[:1500]

        try:
            plan = _RoadmapPlan.model_validate(result.data)
        except Exception as ex:
            self.last_error = f"Roadmap plan failed validation: {str(ex)}"
            return None

        plan.phases = self._sanitize_phases(plan.phases, total_weeks)

        if len(plan.phases) < 3:
            self.last_error = (
                "Roadmap plan contained fewer than 3 usable phases. "
                "Used fallback roadmap."
            )
            return None

        return plan

    def _sanitize_phases(
        self,
        phases: list[_PhasePlan],
        total_weeks: int,
    ) -> list[_PhasePlan]:
        """Drop empty/generic phases, clean blocked terms and generic tasks."""
        cleaned: list[_PhasePlan] = []

        for phase in phases[:8]:
            name = phase.name.strip()

            if not name:
                continue

            tasks = [
                task.strip()
                for task in phase.tasks
                if task.strip()
                and not self._is_generic_task(task)
                and not self._contains_blocked_term(task)
            ]

            if len(tasks) < 2:
                continue

            phase.name = name[:120]
            phase.weeks = max(1, min(int(phase.weeks or 1), 4))
            phase.goal = phase.goal.strip()[:400]
            phase.tasks = tasks[:10]
            phase.deliverables = [
                item.strip() for item in phase.deliverables if item.strip()
            ][:4]
            phase.skillsToLearn = [
                item.strip() for item in phase.skillsToLearn if item.strip()
            ][:4]
            phase.riskWarning = phase.riskWarning.strip()[:250]
            phase.checkpoint = phase.checkpoint.strip()[:250]

            cleaned.append(phase)

        return cleaned[: max(3, min(len(cleaned), total_weeks))]

    def _is_generic_task(self, task: str) -> bool:
        lowered = task.lower()
        return any(phrase in lowered for phrase in self.generic_task_phrases)

    def _contains_blocked_term(self, text: str) -> bool:
        lowered = text.lower()
        return any(term in lowered for term in self.blocked_terms)

    def _build_phase_prompt(
        self,
        request: ProjectRoadmapRequest,
        total_weeks: int,
    ) -> str:
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

        missing = self._split_items(request.missingSkills)
        missing_text = ", ".join(missing) if missing else "None"

        weekly_capacity = request.teamSize * request.availableHoursPerWeek

        if weekly_capacity <= 12:
            capacity_note = (
                "Capacity is LOW. Keep phases lean, prefer fewer parallel tasks, "
                "and give implementation-heavy phases more weeks."
            )
        elif weekly_capacity <= 30:
            capacity_note = (
                "Capacity is MODERATE. Balance learning and implementation."
            )
        else:
            capacity_note = (
                "Capacity is HIGH. Implementation phases can be compressed and "
                "parallel workstreams are possible."
            )

        return f"""
You are ProjectRoadmapAgent inside FYPilot, an Academic Intelligence System for Final Year Project planning.

Design the PHASE PLAN for implementing this specific final year project. You design phases; the platform will expand them into a weekly schedule.

Project idea:
- Title: {request.ideaTitle}
- Problem statement: {request.problemStatement}
- Domain: {request.domain}
- Required technologies: {request.requiredTechnologies}
- Required skills: {request.requiredSkills}
- Missing skills (must be learned): {missing_text}
- Difficulty level: {request.difficultyLevel}
- Final deliverables: {request.finalDeliverables}

Student/team:
- Team size: {request.teamSize}
- Available hours per week per member: {request.availableHoursPerWeek}
- Total weekly capacity: about {weekly_capacity} hours
- Existing skills: {skills_text}
- Skill ratings: {ratings_text}
- {capacity_note}

Timeline:
- Total project duration: {total_weeks} weeks.
- Design 4 to 7 phases. Each phase lasts 1 to 4 weeks ("weeks" field).
- Phase durations should roughly add up to {total_weeks} weeks (the platform will adjust exact numbers).
- Give MORE weeks to the phases that are hardest FOR THIS SPECIFIC PROJECT AND TEAM: complex core features, AI/data work, and areas where skills are missing. Give FEWER weeks to easy or routine phases.

Phase design rules:
- The phase structure must fit THIS project. A data/AI-heavy project needs dataset/model/evaluation phases. A workflow web app needs core-workflow phases named after its real features. Do NOT use a generic template.
- Phase names must mention the project's actual features or components, not generic labels like "Core Feature Development".
- Every task must be concrete and name a real feature, screen, entity, endpoint, or technology from this project. 
  BAD: "Implement main features". 
  GOOD: "Implement the medicine reminder scheduling form and store reminders in PostgreSQL".
- The FIRST phase must include learning tasks for each missing skill: {missing_text}.
- 4 to 10 tasks per phase, 2 to 4 deliverables per phase, 0 to 4 skillsToLearn per phase.
- riskWarning: one short sentence about the biggest risk of that phase for this team.
- checkpoint: one short sentence describing what the supervisor should verify at the end of the phase.
- Technology constraints: use ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, Bootstrap, JavaScript, and Ollama where relevant. Do not suggest React, Node.js, Flutter, AWS, Azure, Kubernetes, blockchain, Flask, Balsamiq, or Figma.
- Return only valid JSON. No markdown, no explanations, no code fences.

Return exactly this JSON structure:

{{
  "roadmapTitle": "string",
  "teamStrategy": "1-2 sentences on how this specific team should divide and pace the work",
  "finalAdvice": "1-2 sentences of project-specific advice",
  "phases": [
    {{
      "name": "string",
      "weeks": 2,
      "goal": "string",
      "tasks": ["string"],
      "deliverables": ["string"],
      "skillsToLearn": ["string"],
      "riskWarning": "string",
      "checkpoint": "string"
    }}
  ]
}}
"""

    # =========================================================================
    # Deterministic expansion: phases -> weeks
    # =========================================================================

    def _expand_plan_to_weeks(
        self,
        request: ProjectRoadmapRequest,
        plan: _RoadmapPlan,
        total_weeks: int,
    ) -> ProjectRoadmapResponse:
        phases = plan.phases
        durations = self._normalize_phase_durations(
            proposed=[phase.weeks for phase in phases],
            total_weeks=total_weeks,
        )

        missing_skills = self._split_items(request.missingSkills)

        weeks: list[RoadmapWeek] = []
        week_number = 1

        for phase, duration in zip(phases, durations):
            task_chunks = self._chunk_tasks(phase.tasks, duration)

            for week_in_phase in range(1, duration + 1):
                is_last_week = week_in_phase == duration
                week_tasks = self._ensure_task_count(
                    tasks=task_chunks[week_in_phase - 1],
                    phase=phase,
                )

                weeks.append(
                    RoadmapWeek(
                        weekNumber=week_number,
                        phaseTitle=phase.name,
                        mainGoal=self._week_goal(phase, week_in_phase, duration),
                        tasks=week_tasks,
                        deliverables=self._week_deliverables(
                            phase, is_last_week
                        ),
                        teamResponsibilities=self._derive_responsibilities(
                            request, week_tasks, phase
                        ),
                        skillsToLearn=self._week_skills(
                            phase, missing_skills, week_number
                        ),
                        riskWarning=(
                            phase.riskWarning
                            or "Avoid expanding the scope beyond this phase's goal."
                        ),
                        checkpoint=self._week_checkpoint(
                            phase, is_last_week
                        ),
                    )
                )

                week_number += 1

        return ProjectRoadmapResponse(
            roadmapTitle=self._clean_text(
                plan.roadmapTitle,
                f"Implementation Roadmap for {request.ideaTitle}",
            ),
            totalWeeks=total_weeks,
            difficultyLevel=request.difficultyLevel,
            teamStrategy=self._clean_text(
                plan.teamStrategy,
                self._default_team_strategy(request),
            ),
            weeks=weeks,
            finalAdvice=self._clean_text(
                plan.finalAdvice,
                (
                    "Start with the MVP, review progress weekly, and avoid adding "
                    "advanced features before the core workflow is stable."
                ),
            ),
        )

    def _normalize_phase_durations(
        self,
        proposed: list[int],
        total_weeks: int,
    ) -> list[int]:
        """
        Scale the LLM-proposed phase durations so they sum EXACTLY to
        total_weeks, preserving their relative weights (largest-remainder
        method), with a minimum of 1 week per phase.
        """
        count = len(proposed)

        if count == 0:
            return []

        if count >= total_weeks:
            # One week per phase for the first total_weeks phases.
            return [1] * total_weeks + [0] * 0 if count == total_weeks else [1] * count

        cleaned = [max(1, int(value or 1)) for value in proposed]
        proposed_total = sum(cleaned)

        # Ideal (fractional) durations preserving relative weights.
        ideal = [value * total_weeks / proposed_total for value in cleaned]

        # Floor with minimum 1, then distribute the remainder by largest
        # fractional part.
        floors = [max(1, int(value)) for value in ideal]
        remainder = total_weeks - sum(floors)

        if remainder > 0:
            fractional = sorted(
                range(count),
                key=lambda i: ideal[i] - int(ideal[i]),
                reverse=True,
            )
            for i in range(remainder):
                floors[fractional[i % count]] += 1
        elif remainder < 0:
            # Took too much because of the min-1 rule: shrink the largest
            # phases first, never below 1.
            for _ in range(-remainder):
                largest = max(range(count), key=lambda i: floors[i])
                if floors[largest] > 1:
                    floors[largest] -= 1

        return floors

    def _chunk_tasks(self, tasks: list[str], duration: int) -> list[list[str]]:
        """Split a phase's tasks across its weeks in order (contiguous
        chunks), so early-phase tasks land in early weeks."""
        if duration <= 1:
            return [list(tasks)]

        chunks: list[list[str]] = [[] for _ in range(duration)]

        for index, task in enumerate(tasks):
            chunks[min(index * duration // max(1, len(tasks)), duration - 1)].append(task)

        return chunks

    def _ensure_task_count(
        self,
        tasks: list[str],
        phase: _PhasePlan,
    ) -> list[str]:
        """Guarantee 3-5 tasks per week without inventing content: pad with
        review/refinement variants of the phase's own tasks."""
        result = list(tasks)

        pad_sources = [task for task in phase.tasks if task not in result]
        pad_templates = [
            "Review and refine: {task}",
            "Test and verify: {task}",
        ]

        template_index = 0
        source_index = 0

        while len(result) < 3:
            if pad_sources:
                source = pad_sources[source_index % len(pad_sources)]
                source_index += 1
            elif phase.tasks:
                source = phase.tasks[source_index % len(phase.tasks)]
                source_index += 1
            else:
                result.append(f"Progress the {phase.name} work and record blockers.")
                continue

            candidate = pad_templates[template_index % len(pad_templates)].format(
                task=self._lower_first(source)
            )
            template_index += 1

            if candidate not in result:
                result.append(candidate)

        return result[:5]

    def _week_goal(self, phase: _PhasePlan, week_in_phase: int, duration: int) -> str:
        goal = phase.goal or f"Advance the {phase.name} phase."

        if duration == 1:
            return goal

        if week_in_phase == 1:
            return f"Start: {goal}"

        if week_in_phase == duration:
            return f"Complete: {goal}"

        return f"Continue (week {week_in_phase} of {duration}): {goal}"

    def _week_deliverables(
        self,
        phase: _PhasePlan,
        is_last_week: bool,
    ) -> list[str]:
        if is_last_week and phase.deliverables:
            return phase.deliverables[:3]

        if phase.deliverables:
            return [f"Progress update: {phase.deliverables[0]}"]

        return [f"Progress update on {phase.name}"]

    def _week_checkpoint(self, phase: _PhasePlan, is_last_week: bool) -> str:
        if is_last_week:
            return (
                phase.checkpoint
                or f"Supervisor reviews the completed {phase.name} phase."
            )

        return f"Internal team review of {phase.name} progress."

    def _week_skills(
        self,
        phase: _PhasePlan,
        missing_skills: list[str],
        week_number: int,
    ) -> list[str]:
        skills = list(phase.skillsToLearn)

        # Front-load missing skills in the first two weeks of the roadmap.
        if week_number <= 2:
            for skill in missing_skills:
                if skill not in skills:
                    skills.insert(0, skill)

        if not skills:
            return ["progress tracking"]

        return skills[:4]

    def _derive_responsibilities(
        self,
        request: ProjectRoadmapRequest,
        week_tasks: list[str],
        phase: _PhasePlan,
    ) -> list[str]:
        """
        Responsibilities are DERIVED from the week's actual tasks — one per
        member, guaranteed count, specific to this week, never a canned
        sentence repeated across weeks.
        """
        team_count = max(1, min(request.teamSize, 5))

        if team_count == 1:
            first_task = (
                self._lower_first(week_tasks[0])
                if week_tasks
                else f"progress the {phase.name} work"
            )
            return [f"Student: lead this week's work, starting with {first_task}"]

        responsibilities: list[str] = []

        for member_index in range(team_count):
            if week_tasks:
                task = week_tasks[member_index % len(week_tasks)]
                text = self._lower_first(task)

                if member_index >= len(week_tasks):
                    responsibilities.append(
                        f"Member {member_index + 1}: support and verify {text}"
                    )
                else:
                    responsibilities.append(
                        f"Member {member_index + 1}: own {text}"
                    )
            else:
                responsibilities.append(
                    f"Member {member_index + 1}: progress the {phase.name} work."
                )

        return responsibilities

    def _lower_first(self, text: str) -> str:
        clean = text.strip().rstrip(".")

        if not clean:
            return clean

        return clean[0].lower() + clean[1:]

    # =========================================================================
    # Ollama JSON parsing
    # =========================================================================

    # =========================================================================
    # Fallback path (unchanged behavior from v1, used only when the LLM fails)
    # =========================================================================

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

        if self._contains_blocked_term(text):
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