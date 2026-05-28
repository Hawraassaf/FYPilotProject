from fastapi import APIRouter, Depends, HTTPException
from app.auth import verify_token
from app.services.risk_engine import predict_risk
from app.services.burndown_engine import compute_burndown
from app.services.grade_predictor import predict_grade
from app.services.anomaly_detector import detect_anomalies
from app.services.analytics_engine import get_analytics
from app.models.schemas import (
    RiskPredictionResponse, BurndownResponse, GradePredictionResponse,
    AnomalyResponse, AnalyticsDashboardResponse,
)

router = APIRouter(prefix="/ds/analytics", tags=["Analytics"])


@router.get("/risk/{project_id}", response_model=RiskPredictionResponse)
def get_risk(project_id: int, user=Depends(verify_token)):
    try:
        return predict_risk(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/burndown/{project_id}", response_model=BurndownResponse)
def get_burndown(project_id: int, user=Depends(verify_token)):
    try:
        return compute_burndown(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/grade/{project_id}", response_model=GradePredictionResponse)
def get_grade_prediction(project_id: int, user=Depends(verify_token)):
    try:
        return predict_grade(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/anomalies/{project_id}", response_model=AnomalyResponse)
def get_anomalies(project_id: int, user=Depends(verify_token)):
    try:
        return detect_anomalies(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/dashboard/{project_id}", response_model=AnalyticsDashboardResponse)
def get_dashboard(project_id: int, user=Depends(verify_token)):
    try:
        return get_analytics(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
