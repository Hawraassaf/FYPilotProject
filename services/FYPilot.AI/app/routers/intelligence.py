from fastapi import APIRouter, Depends, HTTPException
from app.auth import verify_token
from app.services.roadmap_generator import generate_roadmap
from app.services.similarity_checker import check_similarity
from app.services.supervisor_matcher import match_supervisors
from app.services.skill_gap_ml import analyze_skill_gap
from app.models.schemas import (
    RoadmapRequest, RoadmapResponse,
    SimilarityResponse,
    SupervisorMatchRequest, SupervisorMatchResponse,
    SkillGapRequest, SkillGapResponse,
)

router = APIRouter(prefix="/ds/intelligence", tags=["Intelligence"])


@router.post("/roadmap", response_model=RoadmapResponse)
def create_roadmap(req: RoadmapRequest, user=Depends(verify_token)):
    return generate_roadmap(req)


@router.get("/similarity/{project_id}", response_model=SimilarityResponse)
def get_similarity(project_id: int, user=Depends(verify_token)):
    try:
        return check_similarity(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/supervisor-match", response_model=SupervisorMatchResponse)
def get_supervisor_match(req: SupervisorMatchRequest, user=Depends(verify_token)):
    return match_supervisors(req)


@router.post("/skill-gap", response_model=SkillGapResponse)
def get_skill_gap(req: SkillGapRequest, user=Depends(verify_token)):
    return analyze_skill_gap(req)
