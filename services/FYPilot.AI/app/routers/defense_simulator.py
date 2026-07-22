from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter

from app.agents.defense_simulator.defense_simulator_orchestrator import (
    DefenseSimulatorOrchestrator,
    GenerateDefenseQuestionsRequest,
    EvaluateDefenseAnswerRequest,
    DefenseQuestionDto,
)
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response

router = APIRouter(tags=["Defense Simulator"])


def _build_questions_review_context(request: GenerateDefenseQuestionsRequest) -> ReviewContext:
    profile = request.studentProfile
    idea = request.selectedIdea

    trusted_structural_context = {
        "mode": request.mode,
        "numberOfQuestions": request.numberOfQuestions,
        "teamSize": profile.teamSize,
        "availableHoursPerWeek": profile.availableHoursPerWeek,
        "experienceLevel": profile.experienceLevel,
        "skillRatings": profile.skillRatings,
    }

    roadmap_summary = "\n".join(
        f"Phase {phase.phaseNumber}: {phase.name} - {phase.objective}"
        for phase in request.roadmap
    )

    untrusted_project_text = {
        "major": profile.major,
        "studentSkills": ", ".join(profile.skills),
        "ideaTitle": idea.title,
        "problemStatement": idea.problemStatement,
        "targetUsers": idea.targetUsers,
        "whyUseful": idea.whyUseful,
        "requiredTechnologies": idea.requiredTechnologies,
        "requiredSkills": idea.requiredSkills,
        "missingSkills": idea.missingSkills,
        "domain": idea.domain,
        "finalDeliverables": idea.finalDeliverables,
        "roadmapSummary": roadmap_summary,
        "seDocumentation": str(request.seDocumentation or {})[:3000],
    }

    return ReviewContext(
        agent_name="DefenseQuestionAgent",
        trusted_system_instructions=(
            "DefenseQuestionAgent: generates final year project defense "
            "questions tailored to the student's selected idea, roadmap, "
            "and SE documentation. Must never imply confirmed project "
            "features (security audits, encryption, completed deployment) "
            "unless explicitly present in the provided context."
        ),
        trusted_structural_context=trusted_structural_context,
        untrusted_project_text=untrusted_project_text,
        untrusted_user_input="",
        untrusted_conversation_history=[],
        untrusted_existing_code=None,
        untrusted_retrieved_web_content=[],
        previous_model_outputs=[],
        allowed_source_metadata=[],
    )


def _build_evaluation_review_context(request: EvaluateDefenseAnswerRequest) -> ReviewContext:
    profile = request.studentProfile
    idea = request.selectedIdea
    question = request.question

    trusted_structural_context = {"mode": request.mode}

    untrusted_project_text = {
        "questionCategory": question.category,
        "questionDifficulty": question.difficulty,
        "question": question.question,
        "expectedAnswerPoints": ", ".join(question.expectedAnswerPoints),
        "ideaTitle": idea.title if idea else "",
        "problemStatement": idea.problemStatement if idea else "",
        "requiredTechnologies": idea.requiredTechnologies if idea else "",
        "studentSkills": ", ".join(profile.skills) if profile else "",
    }

    return ReviewContext(
        agent_name="DefenseEvaluatorAgent",
        trusted_system_instructions=(
            "DefenseEvaluatorAgent: evaluates a student's defense answer "
            "against the expected answer points for one defense question. "
            "The score-to-level mapping is always computed deterministically, "
            "never written by the LLM. Must never imply confirmed project "
            "features unless explicitly present in the provided context."
        ),
        trusted_structural_context=trusted_structural_context,
        untrusted_project_text=untrusted_project_text,
        untrusted_user_input=request.studentAnswer,
        untrusted_conversation_history=[],
        untrusted_existing_code=None,
        untrusted_retrieved_web_content=[],
        previous_model_outputs=[],
        allowed_source_metadata=[],
    )


@router.get("/defense-simulator/test")
def test_defense_simulator_router() -> Dict[str, Any]:
    return {
        "message": "Defense Simulator router is working",
        "availableEndpoints": [
            "POST /defense-simulator/generate-questions",
            "POST /defense-simulator/evaluate-answer",
        ],
    }


@router.post("/defense-simulator/generate-questions")
def generate_defense_questions(
    request: GenerateDefenseQuestionsRequest,
) -> Dict[str, Any]:
    orchestrator = DefenseSimulatorOrchestrator()
    context = _build_questions_review_context(request)
    pipeline = ReviewPipeline("DefenseQuestionAgent")
    result = pipeline.run(
        lambda: orchestrator.generate_questions_candidate(request),
        context,
        writer_trusted_parts=context.trusted_text_fields(),
        writer_untrusted_parts=context.untrusted_text_fields(),
    )

    if result.usable:
        questions_payload = result.output.get("questions", [])
        llm_used = True
    else:
        questions_payload = orchestrator.build_safe_questions_fallback(request)["questions"]
        llm_used = False

    questions_dtos = [DefenseQuestionDto(**question) for question in questions_payload]

    return {
        "questions": questions_payload,
        "llmUsed": llm_used,
        "source": orchestrator.question_agent.last_provider if llm_used else "dynamic-fallback",
        "ollamaError": orchestrator.question_agent.last_error,
        "modelUsed": orchestrator.question_agent.last_model_used or request.model,
        "consistencyWarnings": orchestrator.build_defense_consistency_warnings(questions_dtos),
        "message": "Defense questions generated successfully",
        "review": build_review_response(result),
        "generatedAt": datetime.utcnow().isoformat(),
    }


@router.post("/defense-simulator/evaluate-answer")
def evaluate_defense_answer(
    request: EvaluateDefenseAnswerRequest,
) -> Dict[str, Any]:
    orchestrator = DefenseSimulatorOrchestrator()
    context = _build_evaluation_review_context(request)
    pipeline = ReviewPipeline("DefenseEvaluatorAgent")
    result = pipeline.run(
        lambda: orchestrator.generate_evaluation_candidate(request),
        context,
        writer_trusted_parts=context.trusted_text_fields(),
        writer_untrusted_parts=context.untrusted_text_fields(),
    )

    if result.usable:
        evaluation = result.output
        llm_used = True
    else:
        evaluation = orchestrator.build_safe_evaluation_fallback(request)
        llm_used = False

    return {
        **evaluation,
        "llmUsed": llm_used,
        "source": orchestrator.evaluator_agent.last_provider if llm_used else "dynamic-fallback",
        "ollamaError": orchestrator.evaluator_agent.last_error,
        "modelUsed": orchestrator.evaluator_agent.last_model_used or request.model,
        "review": build_review_response(result),
        "evaluatedAt": datetime.utcnow().isoformat(),
    }