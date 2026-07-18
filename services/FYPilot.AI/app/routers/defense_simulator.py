from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter

from app.agents.defense_simulator.defense_simulator_orchestrator import (
    DefenseSimulatorOrchestrator,
    GenerateDefenseQuestionsRequest,
    EvaluateDefenseAnswerRequest,
)

router = APIRouter(tags=["Defense Simulator"])


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
    result = orchestrator.generate_questions(request)

    return {
        **result.model_dump(),
        "generatedAt": datetime.utcnow().isoformat(),
    }


@router.post("/defense-simulator/evaluate-answer")
def evaluate_defense_answer(
    request: EvaluateDefenseAnswerRequest,
) -> Dict[str, Any]:
    orchestrator = DefenseSimulatorOrchestrator()
    result = orchestrator.evaluate_answer(request)

    return {
        **result.model_dump(),
        "evaluatedAt": datetime.utcnow().isoformat(),
    }