"""
TEMPORARILY RETAINED FOR MIGRATION COMPATIBILITY — superseded by the shared
review pipeline in app/review/ (see app/routers/fyp_chat.py, which now calls
ReviewPipeline instead of this class). Do not remove until the new
pipeline's comparison tests and the Mentor Chat pilot have both passed. Its
risky-claim knowledge has been copied (not moved) into
app/review/registry.py, reframed as domain hints fed into the semantic
Reviewer's prompt rather than blind regex replacement.

AnswerReviewAgent — deterministic review layer for AI-generated mentor answers.

Runs AFTER the mentor agent's own validation (on the validated answer dict,
i.e. FypMentorAnswer.model_dump()), and before the answer is returned/stored.

This agent is intentionally deterministic (no LLM call):
- Softens risky unsupported claims (word-boundary safe).
- Detects generic/repetitive answers and replaces them with a practical,
  context-aware answer built from the student's real roadmap.
- Preserves the FypMentorAnswer contract exactly (confidence stays an
  int 0-95; suggestedNextActions max 4).

Suggested wiring in the /fyp-chat router:

    result = agent.chat(request)
    review = AnswerReviewAgent().review_mentor_answer(
        answer=result.model_dump(),
        user_message=request.message,
        selected_idea=request.selectedIdea.model_dump() if request.selectedIdea else None,
        roadmap=[p.model_dump() for p in request.roadmap],
    )
    # Return review.revised_answer instead of the raw answer, and expose
    # review.review_score / review.issues for diagnostics.
"""

import re
from typing import Any, Optional

from pydantic import BaseModel, Field


class ReviewIssue(BaseModel):
    category: str
    message: str
    severity: str = "medium"


class ReviewResult(BaseModel):
    approved: bool
    revised_answer: dict[str, Any]
    issues: list[ReviewIssue] = Field(default_factory=list)
    review_score: int = 100
    reviewer_used: bool = True


class AnswerReviewAgent:
    """
    Reviews AI-generated answers before showing them to the user.
    """

    # The mentor contract: confidence is an integer 0-95, max 4 actions.
    MAX_CONFIDENCE = 95
    DEFAULT_CONFIDENCE = 70
    MAX_ACTIONS = 4
    MAX_REPLY_LENGTH = 5000

    def __init__(self):
        # Claims the mentor must not make (or stack items the project does
        # not use). Replacements are applied with word boundaries so partial
        # words are never touched ("flaws" must not match "AWS").
        self.risky_claim_replacements = {
            "ASP.NET Core Identity": "the .NET authentication system",
            "data is encrypted": "data should be protected using authentication, authorization, and ownership checks",
            "database encryption": "database security controls",
            "regular security audits": "security review before deployment",
            "deployed to production": "prepared for deployment",
            "production-ready": "ready for academic demo and further deployment preparation",
            "React frontend": "ASP.NET Core Razor Pages interface",
            "Node.js backend": "ASP.NET Core backend",
            "Flask": "FastAPI",
            "AWS": "the deployment environment",
            "Azure": "the deployment environment",
            "Kubernetes": "the deployment infrastructure",
        }

        # Precompile word-boundary patterns once.
        self._replacement_patterns = [
            (re.compile(rf"\b{re.escape(risky)}\b", re.IGNORECASE), safe)
            for risky, safe in self.risky_claim_replacements.items()
        ]

        self.generic_phrases = [
            "let's start by reviewing the project idea and roadmap",
            "reviewing the project idea and roadmap to ensure we're aligned",
            "how can i assist you",
            "what would you like to know",
            "it depends on your project",
        ]

        self.fear_phrases = [
            "afraid",
            "scared",
            "anxious",
            "worried",
            "overwhelmed",
            "stress",
            "panic",
        ]

    # =========================================================================
    # Main entry point
    # =========================================================================

    def review_mentor_answer(
        self,
        answer: dict[str, Any],
        user_message: str,
        selected_idea: Optional[dict[str, Any]] = None,
        roadmap: Optional[list[dict[str, Any]]] = None,
    ) -> ReviewResult:
        issues: list[ReviewIssue] = []

        revised = dict(answer)

        reply = str(revised.get("reply", "")).strip()

        if not reply:
            reply = self._practical_reply(user_message, selected_idea, roadmap)
            issues.append(
                ReviewIssue(
                    category="empty_answer",
                    message="The AI answer was empty, so a fallback answer was generated.",
                    severity="high",
                )
            )
        elif self._is_generic(reply):
            issues.append(
                ReviewIssue(
                    category="generic_answer",
                    message="The AI answer was too generic or repetitive.",
                    severity="medium",
                )
            )
            reply = self._practical_reply(user_message, selected_idea, roadmap)

        cleaned_reply = self._clean_risky_claims(reply)

        if cleaned_reply != reply:
            issues.append(
                ReviewIssue(
                    category="unsupported_claims",
                    message="Unsupported or risky claims were softened before showing the answer.",
                    severity="medium",
                )
            )

        revised["reply"] = cleaned_reply[: self.MAX_REPLY_LENGTH]

        revised["suggestedNextActions"] = self._clean_actions(
            revised.get("suggestedNextActions", []),
            roadmap,
        )

        revised["warning"] = self._clean_risky_claims(
            str(revised.get("warning") or "")
        )

        revised["assumptions"] = self._clean_list(
            revised.get("assumptions", []), self.MAX_ACTIONS
        )

        # CONTRACT FIX: confidence must remain an INTEGER 0-95 (the mentor
        # response model and the .NET DTO both use int). The previous
        # version converted it to a 0.0-1.0 float, which broke validation.
        revised["confidence"] = self._normalize_confidence(
            revised.get("confidence", self.DEFAULT_CONFIDENCE)
        )

        score = self._score_answer(revised, issues)

        return ReviewResult(
            approved=score >= 70,
            revised_answer=revised,
            issues=issues,
            review_score=score,
            reviewer_used=True,
        )

    # =========================================================================
    # Detection and cleaning
    # =========================================================================

    def _is_generic(self, text: str) -> bool:
        lowered = text.lower()
        return any(phrase in lowered for phrase in self.generic_phrases)

    def _clean_risky_claims(self, text: str) -> str:
        if not text:
            return ""

        cleaned = text

        for pattern, safe in self._replacement_patterns:
            cleaned = pattern.sub(safe, cleaned)

        return cleaned.strip()

    # =========================================================================
    # Context-aware practical fallback
    # =========================================================================

    def _practical_reply(
        self,
        user_message: str,
        selected_idea: Optional[dict[str, Any]],
        roadmap: Optional[list[dict[str, Any]]],
    ) -> str:
        title = "your final year project"

        if selected_idea:
            title = (
                selected_idea.get("title")
                or selected_idea.get("Title")
                or title
            )

        # Emotional reassurance only when the student actually expressed
        # fear/stress — a generic answer to a technical question must not be
        # replaced with consolation.
        opener = ""
        if self._mentions_fear(user_message):
            opener = "It is normal to feel this way at the beginning. "

        next_phase_name = self._next_incomplete_phase_name(roadmap)

        if next_phase_name:
            return (
                f"{opener}Focus on one concrete step for {title}: your next roadmap "
                f"phase is '{next_phase_name}'. Work only on that phase this week. "
                "Three practical actions for today: confirm this phase's scope, pick "
                "its single most important task, and implement or test one small piece "
                "of it end-to-end. Progress on one real feature beats planning ten."
            )

        return (
            f"{opener}Take one concrete step on {title} instead of the whole system "
            "at once: 1) confirm the exact problem and target users, 2) define the "
            "MVP features, 3) implement one simple feature end-to-end before adding "
            "AI or advanced modules."
        )

    def _mentions_fear(self, user_message: str) -> bool:
        lowered = (user_message or "").lower()
        return any(phrase in lowered for phrase in self.fear_phrases)

    def _next_incomplete_phase_name(
        self,
        roadmap: Optional[list[dict[str, Any]]],
    ) -> Optional[str]:
        """First phase that is not completed (previously roadmap[0] was used,
        which could be a phase the student already finished)."""
        if not roadmap:
            return None

        def phase_number(phase: dict[str, Any]) -> int:
            for key in ("phaseNumber", "PhaseNumber", "weekNumber", "WeekNumber"):
                value = phase.get(key)
                if isinstance(value, int):
                    return value
            return 0

        def is_completed(phase: dict[str, Any]) -> bool:
            for key in ("isCompleted", "IsCompleted", "completed", "Completed"):
                if key in phase:
                    return bool(phase.get(key))
            return False

        def name_of(phase: dict[str, Any]) -> Optional[str]:
            for key in ("name", "Name", "phaseTitle", "PhaseTitle", "title", "Title"):
                value = phase.get(key)
                if value:
                    return str(value)
            return None

        for phase in sorted(roadmap, key=phase_number):
            if not is_completed(phase):
                return name_of(phase)

        return None

    # =========================================================================
    # Actions / lists / confidence
    # =========================================================================

    def _clean_actions(
        self,
        actions: Any,
        roadmap: Optional[list[dict[str, Any]]],
    ) -> list[str]:
        cleaned = self._clean_list(actions, self.MAX_ACTIONS)

        cleaned = [
            self._clean_risky_claims(action)
            for action in cleaned
            if not self._is_generic(action)
        ]

        if cleaned:
            return cleaned[: self.MAX_ACTIONS]

        if roadmap:
            next_phase = self._next_incomplete_phase_name(roadmap)
            first_action = (
                f"Open the roadmap and work on the '{next_phase}' phase."
                if next_phase
                else "Open the roadmap and complete the next phase tasks."
            )
            return [
                first_action,
                "Write the first 3 functional requirements.",
                "Test one small feature end-to-end before adding more features.",
            ]

        return [
            "Define the MVP features clearly.",
            "Create the first database entities.",
            "Build one small working flow before adding advanced AI features.",
        ]

    def _clean_list(self, value: Any, max_items: int) -> list[str]:
        if not value:
            return []

        if isinstance(value, list):
            return [
                str(item).strip()
                for item in value
                if str(item).strip()
            ][:max_items]

        return [str(value).strip()] if str(value).strip() else []

    def _normalize_confidence(self, value: Any) -> int:
        """
        Contract: integer 0-95.

        Accepts legacy inputs defensively:
        - 0-1 floats (e.g. 0.75) are treated as fractions and scaled to 75.
        - Anything else is rounded and clamped to 0-95.
        """
        try:
            number = float(value)
        except Exception:
            return self.DEFAULT_CONFIDENCE

        if 0.0 <= number <= 1.0:
            number *= 100.0

        return int(max(0, min(round(number), self.MAX_CONFIDENCE)))

    # =========================================================================
    # Scoring
    # =========================================================================

    def _score_answer(
        self,
        answer: dict[str, Any],
        issues: list[ReviewIssue],
    ) -> int:
        score = 100

        for issue in issues:
            if issue.severity == "high":
                score -= 30
            elif issue.severity == "medium":
                score -= 15
            else:
                score -= 5

        reply = str(answer.get("reply", "")).strip()

        if len(reply) < 40:
            score -= 20

        if not answer.get("suggestedNextActions"):
            score -= 10

        return max(0, min(score, 100))
