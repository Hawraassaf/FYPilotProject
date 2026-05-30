"""
POST /generate-ideas — Generate 3 personalized FYP ideas based on student profile.

This endpoint uses ProjectIdeaAgent to generate ideas with deterministic scoring.
All scores are calculated from input parameters, not invented by LLM.
"""

import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents import ProjectIdeaAgent
from app.agents.project_idea_agent import (
    StudentProfile,
    ProjectIdea,
    IdeaGenerationResponse,
)

logger = logging.getLogger("fypilot-ideas")

router = APIRouter(
    prefix="/ideas",
    tags=["ideas"],
)


class GenerateIdeasRequest(BaseModel):
    """Request payload for POST /generate-ideas."""
    studentSkills: list[str] = Field(
        ..., description="List of student skills (e.g., ['Python', 'React', 'SQL'])"
    )
    skillRatings: dict[str, int] = Field(
        ..., description="Rating (1-5) for each skill"
    )
    major: str = Field(..., description="Student major (e.g., 'Computer Science')")
    experienceLevel: int = Field(
        ..., ge=0, le=5, description="Experience level (0=beginner, 5=expert)"
    )
    preferredDomain: str = Field(
        ..., description="Preferred domain (e.g., 'Web Development', 'AI/ML')"
    )
    targetDifficulty: int = Field(
        ..., ge=1, le=5, description="Target difficulty (1=easy, 5=very hard)"
    )
    availableHoursPerWeek: int = Field(
        ..., ge=1, description="Hours available per week for the project"
    )
    teamSize: int = Field(..., ge=1, description="Team size (1 or more)")
    projectGoals: list[str] = Field(
        ..., description="Project goals (e.g., ['Learn new tech', 'Build portfolio'])"
    )
    lebaneseMarketRelevance: bool = Field(
        ..., description="Should ideas focus on Lebanese market relevance?"
    )


@router.post(
    "/generate-ideas",
    response_model=IdeaGenerationResponse,
    summary="Generate 3 personalized FYP ideas",
    description=(
        "Generates exactly 3 FYP ideas based on student profile. "
        "All scores are deterministic (innovation, feasibility, market demand). "
        "Uses ProjectIdeaAgent with no LLM score invention."
    ),
)
async def generate_ideas(request: GenerateIdeasRequest):
    """
    Generate 3 personalized final year project ideas.
    
    Returns 3 ideas with 18 fields each:
    - title, problemStatement, targetUsers, whyUseful
    - lebaneseMarketRelevance, requiredTechnologies, requiredSkills, missingSkills
    - difficultyLevel, innovationScore, feasibilityScore, marketDemandScore
    - expectedDurationWeeks, supervisorCategory, datasetNeeded, finalDeliverables
    - domain, lebaneseSector
    
    All scores (innovation, feasibility, market demand) are deterministic calculations
    based on input parameters, not generated or invented by any LLM.
    """
    try:
        # Build StudentProfile from request
        profile = StudentProfile(
            studentSkills=request.studentSkills,
            skillRatings=request.skillRatings,
            major=request.major,
            experienceLevel=request.experienceLevel,
            preferredDomain=request.preferredDomain,
            targetDifficulty=request.targetDifficulty,
            availableHoursPerWeek=request.availableHoursPerWeek,
            teamSize=request.teamSize,
            projectGoals=request.projectGoals,
            lebaneseMarketRelevance=request.lebaneseMarketRelevance,
        )
        
        # Generate ideas using ProjectIdeaAgent
        agent = ProjectIdeaAgent()
        ideas = agent.generate_ideas(profile)
        
        # Verify we have exactly 3 ideas
        if len(ideas) != 3:
            logger.error(f"Agent returned {len(ideas)} ideas instead of 3")
            raise HTTPException(
                status_code=500,
                detail="Agent failed to generate exactly 3 ideas",
            )
        
        # Verify all ideas have all 18 required fields
        for i, idea in enumerate(ideas):
            required_fields = {
                "title", "problemStatement", "targetUsers", "whyUseful",
                "lebaneseMarketRelevance", "requiredTechnologies",
                "requiredSkills", "missingSkills", "difficultyLevel",
                "innovationScore", "feasibilityScore", "marketDemandScore",
                "expectedDurationWeeks", "supervisorCategory", "datasetNeeded",
                "finalDeliverables", "domain", "lebaneseSector",
            }
            idea_fields = set(idea.model_dump().keys())
            missing_fields = required_fields - idea_fields
            if missing_fields:
                logger.error(
                    f"Idea {i+1} is missing fields: {missing_fields}"
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Idea {i+1} is missing required fields: {missing_fields}",
                )
        
        logger.info(f"Successfully generated 3 ideas for student with skills: {request.studentSkills}")
        
        return IdeaGenerationResponse(
            ideas=ideas,
            generatedAt=datetime.now().isoformat(),
        )
    
    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Validation error: {str(e)}")
    except Exception as e:
        logger.error(f"Error generating ideas: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error generating ideas: {str(e)}",
        )
