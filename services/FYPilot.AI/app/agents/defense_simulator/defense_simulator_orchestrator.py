from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.agents.defense_simulator.defense_question_agent import DefenseQuestionAgent
from app.agents.defense_simulator.defense_evaluator_agent import DefenseEvaluatorAgent
from app.services.llm_provider import LLMResult


class DefenseStudentProfileDto(BaseModel):
    major: str = "Computer Science"
    experienceLevel: str = "intermediate"
    teamSize: int = 1
    availableHoursPerWeek: int = 10
    skills: List[str] = Field(default_factory=list)
    skillRatings: Dict[str, int] = Field(default_factory=dict)


class DefenseSelectedIdeaDto(BaseModel):
    id: Optional[int] = None
    title: str = ""
    problemStatement: str = ""
    targetUsers: str = ""
    whyUseful: str = ""
    requiredTechnologies: str = ""
    requiredSkills: str = ""
    missingSkills: str = ""
    difficultyLevel: str = ""
    expectedDurationWeeks: int = 10
    domain: str = ""
    finalDeliverables: str = ""


class DefenseRoadmapPhaseDto(BaseModel):
    phaseNumber: int = 1
    name: str = ""
    objective: str = ""
    tasks: List[str] = Field(default_factory=list)
    expectedOutput: str = ""
    successCriteria: str = ""
    isCompleted: bool = False


class DefenseQuestionDto(BaseModel):
    id: str = ""
    category: str = ""
    difficulty: str = "Medium"
    question: str = ""
    expectedAnswerPoints: List[str] = Field(default_factory=list)
    followUpQuestion: str = ""


class GenerateDefenseQuestionsRequest(BaseModel):
    studentProfile: DefenseStudentProfileDto
    selectedIdea: DefenseSelectedIdeaDto
    roadmap: List[DefenseRoadmapPhaseDto] = Field(default_factory=list)
    seDocumentation: Optional[Dict[str, Any]] = None
    mode: str = "mixed"
    numberOfQuestions: int = Field(default=10, ge=3, le=20)
    model: str = "qwen2.5-coder:7b"


class GenerateDefenseQuestionsResponse(BaseModel):
    questions: List[DefenseQuestionDto]
    llmUsed: bool
    source: str
    ollamaError: Optional[str] = None
    modelUsed: str = "qwen2.5-coder:7b"
    consistencyWarnings: List[str] = Field(default_factory=list)
    message: str = ""


class EvaluateDefenseAnswerRequest(BaseModel):
    question: DefenseQuestionDto
    studentAnswer: str
    studentProfile: Optional[DefenseStudentProfileDto] = None
    selectedIdea: Optional[DefenseSelectedIdeaDto] = None
    mode: str = "mixed"
    model: str = "qwen2.5-coder:7b"


class DefenseQuestionBatch(BaseModel):
    """Review-pipeline candidate shape for DefenseQuestionAgent -- just the
    reviewable question list, without the llmUsed/source/metadata fields
    the orchestrator adds afterward."""

    questions: List[DefenseQuestionDto] = Field(default_factory=list)


class DefenseEvaluationCandidate(BaseModel):
    """Review-pipeline candidate shape for DefenseEvaluatorAgent -- just the
    reviewable evaluation fields, without the llmUsed/source/metadata fields
    the orchestrator adds afterward."""

    score: int = 0
    level: str = ""
    strengths: List[str] = Field(default_factory=list)
    missingPoints: List[str] = Field(default_factory=list)
    improvedAnswer: str = ""
    followUpQuestion: str = ""
    feedbackSummary: str = ""


class EvaluateDefenseAnswerResponse(BaseModel):
    score: int
    level: str
    strengths: List[str]
    missingPoints: List[str]
    improvedAnswer: str
    followUpQuestion: str
    feedbackSummary: str
    llmUsed: bool
    source: str
    ollamaError: Optional[str] = None
    modelUsed: str = "qwen2.5-coder:7b"


class DefenseSimulatorOrchestrator:
    """
    Coordinates defense question generation and answer evaluation.
    """

    VALID_CATEGORIES = {
        "Problem Understanding",
        "Technical Architecture",
        "Database Design",
        "AI Integration",
        "Feasibility",
        "Testing and Validation",
        "Security",
        "Limitations",
        "Future Work",
        "Business Value",
    }

    VALID_DIFFICULTIES = {"Easy", "Medium", "Hard"}

    def __init__(self):
        self.question_agent = DefenseQuestionAgent()
        self.evaluator_agent = DefenseEvaluatorAgent()

    def generate_questions(
        self,
        request: GenerateDefenseQuestionsRequest,
    ) -> GenerateDefenseQuestionsResponse:
        raw = self.question_agent.generate_questions(request)
        questions = self._clean_questions(raw, request)

        llm_used = bool(raw and raw.get("questions"))

        if not questions:
            questions = self._fallback_questions(request)
            llm_used = False

        return GenerateDefenseQuestionsResponse(
            questions=questions,
            llmUsed=llm_used,
            source=self.question_agent.last_provider if llm_used else "dynamic-fallback",
            ollamaError=self.question_agent.last_error,
            modelUsed=self.question_agent.last_model_used or request.model,
            consistencyWarnings=self.build_defense_consistency_warnings(questions),
            message="Defense questions generated successfully",
        )

    def evaluate_answer(
        self,
        request: EvaluateDefenseAnswerRequest,
    ) -> EvaluateDefenseAnswerResponse:
        raw = self.evaluator_agent.evaluate_answer(request)

        llm_used = bool(raw)

        if not raw:
            raw = self._fallback_evaluation(request)
            llm_used = False

        cleaned = self._clean_evaluation_fields(raw)

        return EvaluateDefenseAnswerResponse(
            **cleaned,
            llmUsed=llm_used,
            source=self.evaluator_agent.last_provider if llm_used else "dynamic-fallback",
            ollamaError=self.evaluator_agent.last_error,
            modelUsed=self.evaluator_agent.last_model_used or request.model,
        )

    def _clean_evaluation_fields(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """
        Shared deterministic cleanup used by both evaluate_answer() (the
        existing, still-used response path) and generate_evaluation_candidate()/
        build_safe_evaluation_fallback() (the review-pipeline path) -- extracted
        so both apply IDENTICAL normalization instead of duplicating it.
        """
        strengths = self._clean_string_list(raw.get("strengths", []))
        missing_points = self._clean_string_list(raw.get("missingPoints", []))

        strengths = [
            self._clean_unverified_project_claims(item)
            for item in strengths
            if self._clean_unverified_project_claims(item)
        ]

        missing_points = [
            self._clean_unverified_project_claims(item)
            for item in missing_points
            if self._clean_unverified_project_claims(item)
        ]

        improved_answer = self._clean_text(raw.get("improvedAnswer", ""))
        improved_answer = self._clean_unverified_project_claims(improved_answer)

        follow_up = self._clean_text(raw.get("followUpQuestion", ""))
        follow_up = self._clean_unverified_project_claims(follow_up)

        feedback_summary = self._clean_text(raw.get("feedbackSummary", ""))
        feedback_summary = self._clean_unverified_project_claims(feedback_summary)

        return {
            "score": self._clean_score(raw.get("score", 0)),
            "level": self._clean_level(raw.get("level", "")),
            "strengths": strengths,
            "missingPoints": missing_points,
            "improvedAnswer": improved_answer,
            "followUpQuestion": follow_up,
            "feedbackSummary": feedback_summary,
        }

    # =========================================================================
    # Review pipeline integration (app/review/pipeline.py)
    # =========================================================================

    def generate_questions_candidate(
        self,
        request: GenerateDefenseQuestionsRequest,
    ) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Reuses generate_questions()'s
        underlying LLM call + deterministic cleanup (_clean_questions: category/
        difficulty coercion, sequential DQ-NN ids, count padding) rather than
        duplicating it, then wraps the result as an LLMResult.

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- whenever
        the LLM call itself failed (no questions dict returned at all), since
        in that case there is no real candidate to review; the router should
        use build_safe_questions_fallback() directly instead.
        """
        raw = self.question_agent.generate_questions(request)
        questions = self._clean_questions(raw, request)
        llm_used = bool(raw and raw.get("questions"))

        if not llm_used or not questions:
            return None

        return LLMResult(
            ok=True,
            provider=self.question_agent.last_provider or "unknown",
            model=self.question_agent.last_model_used,
            text="",
            data={"questions": [question.model_dump() for question in questions]},
        )

    def build_safe_questions_fallback(
        self,
        request: GenerateDefenseQuestionsRequest,
    ) -> Dict[str, Any]:
        """
        Public entry point for the deterministic fallback question bank --
        the same template bank generate_questions() already falls back to
        internally when the LLM call fails, exposed publicly so routers
        never reach into a private method.
        """
        questions = self._fallback_questions(request)
        return {"questions": [question.model_dump() for question in questions]}

    def generate_evaluation_candidate(
        self,
        request: EvaluateDefenseAnswerRequest,
    ) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Reuses the evaluator's
        LLM call + the same deterministic cleanup evaluate_answer() applies
        (_clean_evaluation_fields), then wraps the result as an LLMResult.

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- whenever
        the LLM call itself failed, since in that case there is no real
        candidate to review; the router should use
        build_safe_evaluation_fallback() directly instead.
        """
        raw = self.evaluator_agent.evaluate_answer(request)

        if not raw:
            return None

        cleaned = self._clean_evaluation_fields(raw)

        return LLMResult(
            ok=True,
            provider=self.evaluator_agent.last_provider or "unknown",
            model=self.evaluator_agent.last_model_used,
            text="",
            data=cleaned,
        )

    def build_safe_evaluation_fallback(
        self,
        request: EvaluateDefenseAnswerRequest,
    ) -> Dict[str, Any]:
        """
        Public entry point for the deterministic fallback evaluation -- the
        same word-count/keyword-matching template evaluate_answer() already
        falls back to internally when the LLM call fails, exposed publicly
        so routers never reach into a private method.
        """
        raw = self._fallback_evaluation(request)
        return self._clean_evaluation_fields(raw)

    def _clean_questions(
        self,
        raw: Dict[str, Any],
        request: GenerateDefenseQuestionsRequest,
    ) -> List[DefenseQuestionDto]:
        if not isinstance(raw, dict):
            return []

        raw_questions = raw.get("questions", [])

        if not isinstance(raw_questions, list):
            return []

        cleaned: List[DefenseQuestionDto] = []
        number = request.numberOfQuestions

        for index, item in enumerate(raw_questions[:number]):
            if not isinstance(item, dict):
                continue

            category = self._clean_text(item.get("category", ""))

            if category not in self.VALID_CATEGORIES:
                category = self._default_category(index)

            difficulty = self._clean_text(item.get("difficulty", "Medium")).capitalize()

            if difficulty not in self.VALID_DIFFICULTIES:
                difficulty = "Medium"

            question_text = self._clean_text(item.get("question", ""))
            question_text = self._clean_unverified_project_claims(question_text)

            if not question_text:
                continue

            expected_points = self._clean_string_list(item.get("expectedAnswerPoints", []))
            expected_points = [
                self._clean_unverified_project_claims(point)
                for point in expected_points
                if self._clean_unverified_project_claims(point)
            ]

            if len(expected_points) < 2:
                expected_points = self._default_expected_points(category)

            follow_up = self._clean_text(item.get("followUpQuestion", ""))
            follow_up = self._clean_unverified_project_claims(follow_up)

            if not follow_up:
                follow_up = "Can you explain this point in more detail?"

            cleaned.append(
                DefenseQuestionDto(
                    id=f"DQ-{len(cleaned) + 1:02d}",
                    category=category,
                    difficulty=difficulty,
                    question=question_text,
                    expectedAnswerPoints=expected_points[:5],
                    followUpQuestion=follow_up,
                )
            )

        if len(cleaned) < number:
            fallback = self._fallback_questions(request)
            existing_questions = {q.question for q in cleaned}

            for question in fallback:
                if question.question not in existing_questions:
                    cleaned.append(question)

                if len(cleaned) >= number:
                    break

        return cleaned[:number]

    def _fallback_questions(
        self,
        request: GenerateDefenseQuestionsRequest,
    ) -> List[DefenseQuestionDto]:
        idea = request.selectedIdea

        base_questions = [
            {
                "category": "Problem Understanding",
                "difficulty": "Medium",
                "question": f"What problem does your project '{idea.title}' solve?",
                "points": [
                    "Explain the main problem clearly.",
                    "Mention the target users affected by the problem.",
                    "Explain why the problem is important.",
                    "Connect the problem to final year project planning.",
                ],
                "follow": "How did you validate that this problem really exists?",
            },
            {
                "category": "Business Value",
                "difficulty": "Medium",
                "question": "Who are the main users of your system and how does each user benefit from it?",
                "points": [
                    "Identify students as main users.",
                    "Mention supervisors or academic coordinators if relevant.",
                    "Explain the benefit for each user type.",
                ],
                "follow": "Which user group benefits the most and why?",
            },
            {
                "category": "Technical Architecture",
                "difficulty": "Hard",
                "question": "Why did you separate the ASP.NET Core web application from the Python FastAPI AI service?",
                "points": [
                    "Explain separation of concerns.",
                    "Mention that .NET handles UI, authentication, and database workflow.",
                    "Mention that Python handles AI agents and Ollama integration.",
                    "Explain maintainability and flexibility.",
                ],
                "follow": "What are the disadvantages of using two services?",
            },
            {
                "category": "AI Integration",
                "difficulty": "Hard",
                "question": "How does your system use Ollama, and why did you choose a local LLM?",
                "points": [
                    "Explain that Ollama runs a local model.",
                    "Mention privacy and offline availability.",
                    "Mention avoiding external API cost.",
                    "Mention that AI outputs are validated before display.",
                ],
                "follow": "What happens if Ollama gives wrong or incomplete output?",
            },
            {
                "category": "Database Design",
                "difficulty": "Medium",
                "question": "What are the most important database entities in your project?",
                "points": [
                    "Mention User.",
                    "Mention StudentProfile.",
                    "Mention StudentSkill.",
                    "Mention ProjectIdea.",
                    "Mention generated outputs such as Roadmap or SEDocumentation.",
                ],
                "follow": "How do you ensure each student sees only their own data?",
            },
            {
                "category": "Testing and Validation",
                "difficulty": "Medium",
                "question": "How would you test that the SE Documentation Generator works correctly?",
                "points": [
                    "Test that documentation is generated for a selected idea.",
                    "Validate JSON structure.",
                    "Check requirement references and traceability.",
                    "Test fallback behavior when Ollama is unavailable.",
                ],
                "follow": "What is the difference between testing the endpoint and testing the quality of the generated documentation?",
            },
            {
                "category": "Security",
                "difficulty": "Medium",
                "question": "What security concerns exist in your system?",
                "points": [
                    "Authentication and authorization.",
                    "Protecting student data.",
                    "Preventing access to other users' ideas.",
                    "Handling AI-generated content safely.",
                ],
                "follow": "How would you improve security before deployment?",
            },
            {
                "category": "Feasibility",
                "difficulty": "Medium",
                "question": "Why is this project feasible for your team size and available time?",
                "points": [
                    "Explain the selected MVP scope.",
                    "Mention the technologies you already know.",
                    "Mention roadmap phases.",
                    "Mention that advanced features can be future work.",
                ],
                "follow": "Which feature would you remove first if time becomes limited?",
            },
            {
                "category": "Limitations",
                "difficulty": "Hard",
                "question": "What are the main limitations of your system?",
                "points": [
                    "AI output may need human review.",
                    "Local model can be slow.",
                    "Quality depends on input context.",
                    "The system does not replace supervisor approval.",
                ],
                "follow": "How can you reduce the effect of these limitations?",
            },
            {
                "category": "Future Work",
                "difficulty": "Easy",
                "question": "What future improvements would you add after the first version?",
                "points": [
                    "Supervisor comments.",
                    "PDF or Word export.",
                    "Version history.",
                    "Improved defense simulator.",
                    "Deployment and analytics.",
                ],
                "follow": "Which future feature has the highest priority?",
            },
        ]

        questions: List[DefenseQuestionDto] = []

        for index, item in enumerate(base_questions[: request.numberOfQuestions]):
            questions.append(
                DefenseQuestionDto(
                    id=f"DQ-{index + 1:02d}",
                    category=item["category"],
                    difficulty=item["difficulty"],
                    question=item["question"],
                    expectedAnswerPoints=item["points"],
                    followUpQuestion=item["follow"],
                )
            )

        return questions

    def _fallback_evaluation(
        self,
        request: EvaluateDefenseAnswerRequest,
    ) -> Dict[str, Any]:
        answer = request.studentAnswer.strip()
        expected_points = request.question.expectedAnswerPoints

        if not answer:
            return {
                "score": 0,
                "level": "Weak",
                "strengths": [],
                "missingPoints": expected_points or ["The answer is empty."],
                "improvedAnswer": "A stronger answer should clearly address the question, mention the project context, and explain the technical or academic reasoning.",
                "followUpQuestion": request.question.followUpQuestion,
                "feedbackSummary": "No answer was provided.",
            }

        word_count = len(answer.split())
        score = 50

        if word_count >= 30:
            score += 15

        if word_count >= 70:
            score += 10

        lower_answer = answer.lower()

        matched_points = 0

        for point in expected_points:
            important_words = [
                word.lower()
                for word in point.split()
                if len(word) > 5
            ]

            if any(word in lower_answer for word in important_words):
                matched_points += 1

        if expected_points:
            score += int((matched_points / len(expected_points)) * 20)

        score = max(0, min(85, score))

        missing = []

        for point in expected_points:
            important_words = [
                word.lower()
                for word in point.split()
                if len(word) > 5
            ]

            if not any(word in lower_answer for word in important_words):
                missing.append(point)

        if not missing:
            missing = ["The answer can still be improved with a clearer example and stronger academic wording."]

        return {
            "score": score,
            "level": self._score_to_level(score),
            "strengths": [
                "The answer attempts to address the defense question.",
                "The answer is related to the project context.",
            ],
            "missingPoints": missing[:4],
            "improvedAnswer": (
                "A stronger answer would directly answer the question, explain the reason behind the design choice, "
                "connect it to the project problem, and mention a concrete example from the implementation."
            ),
            "followUpQuestion": request.question.followUpQuestion or "Can you explain this with an example from your implementation?",
            "feedbackSummary": "This is an automatic fallback evaluation because Ollama was not available.",
        }
    
    def build_defense_consistency_warnings(
        self,
        questions: List[DefenseQuestionDto],
    ) -> List[str]:
        warnings: List[str] = []

        risky_terms = [
            "ASP.NET Core Identity",
            "encrypted before storing",
            "regular security audits",
            "security audits will be conducted",
            "deployment is complete",
            "deployed to production",
        ]

        combined_text = " ".join(
            [
                question.question + " "
                + " ".join(question.expectedAnswerPoints) + " "
                + question.followUpQuestion
                for question in questions
            ]
        )

        for term in risky_terms:
            if term.lower() in combined_text.lower():
                warnings.append(
                    f"Potential unverified claim detected and should be reviewed: {term}"
                )

        return list(dict.fromkeys(warnings))[:5]

    def _clean_score(self, value: Any) -> int:
        try:
            score = int(value)
        except Exception:
            score = 0

        return max(0, min(100, score))
    
    def _clean_unverified_project_claims(self, text: str) -> str:
        """
        TEMPORARILY RETAINED FOR MIGRATION COMPATIBILITY. This defense
        simulator is not yet wired into the shared review pipeline
        (app/review/) -- only FYP Mentor Chat is, as the pilot. When the
        defense simulator is migrated, this claim list should move into
        app/review/registry.py the same way app/agents/answer_review_agent.py's
        was, and the semantic Reviewer/Rewrite loop should replace this
        regex-based cleanup. Do not remove until that migration happens.

        Removes or softens risky claims that Ollama may invent.

        The defense simulator should not claim the project has features
        that are not confirmed in the actual implementation.
        """

        if not text:
            return ""

        replacements = {
            "ASP.NET Core Identity": "the .NET authentication system",
            "Data is encrypted before storing it in PostgreSQL": "student data is protected using authentication, authorization, and ownership checks",
            "data is encrypted before storing it in PostgreSQL": "student data is protected using authentication, authorization, and ownership checks",
            "Regular security audits will be conducted": "security should be reviewed before deployment",
            "regular security audits will be conducted": "security should be reviewed before deployment",
            "Documentation validation uses natural language processing techniques": "documentation output is validated using structured JSON checks and cleanup rules",
            "documentation validation uses natural language processing techniques": "documentation output is validated using structured JSON checks and cleanup rules",
            "The 'Ollama' model": "the local Ollama model",
            "The Ollama model": "the local Ollama model",
            "A comparison model": "the idea comparison agent",
            "Natural language processing techniques": "structured AI output validation",
            "natural language processing techniques": "structured AI output validation",
        }

        cleaned = text

        for risky, safe in replacements.items():
            cleaned = cleaned.replace(risky, safe)

        return cleaned.strip()

    def _clean_level(self, value: Any) -> str:
        text = self._clean_text(value)

        valid = {"Excellent", "Very Good", "Good", "Average", "Weak"}

        if text in valid:
            return text

        return self._score_to_level(self._clean_score(value))

    def _score_to_level(self, score: int) -> str:
        if score >= 90:
            return "Excellent"
        if score >= 80:
            return "Very Good"
        if score >= 65:
            return "Good"
        if score >= 50:
            return "Average"
        return "Weak"

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""

        return str(value).strip()

    def _clean_string_list(self, value: Any) -> List[str]:
        if value is None:
            return []

        if isinstance(value, list):
            return [
                self._clean_text(item)
                for item in value
                if self._clean_text(item)
            ]

        if isinstance(value, str):
            parts = value.replace("\n", "|").replace(",", "|").split("|")

            return [
                self._clean_text(item)
                for item in parts
                if self._clean_text(item)
            ]

        return [self._clean_text(value)] if self._clean_text(value) else []

    def _default_category(self, index: int) -> str:
        categories = list(self.VALID_CATEGORIES)
        return categories[index % len(categories)]

    def _default_expected_points(self, category: str) -> List[str]:
        return [
            f"Explain the main idea related to {category}.",
            "Connect the answer to the selected project.",
            "Mention a concrete implementation or design detail.",
        ]