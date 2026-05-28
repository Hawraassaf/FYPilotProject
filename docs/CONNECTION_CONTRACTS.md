# Connection Contracts — .NET ↔ Python

## How .NET Calls the Python AI Service

All communication between .NET and Python goes through one file:

**`src/FYPilot.Infrastructure/Services/AiServiceClient.cs`**

This is the only place in the .NET solution that knows the Python service URL or HTTP details. All other code uses the `IAiServiceClient` interface from `FYPilot.Application`.

---

## Interface Contract

```csharp
// src/FYPilot.Application/Interfaces/IAiServiceClient.cs
public interface IAiServiceClient
{
    Task<AiHealthResponse?>              GetHealthAsync();
    Task<SkillAnalysisResponse?>         AnalyzeSkillsAsync(SkillAnalysisRequest request);
    Task<FeasibilityPredictionResponse?> PredictFeasibilityAsync(FeasibilityPredictionRequest request);
    Task<SimilarityCheckResponse?>       CheckSimilarityAsync(SimilarityCheckRequest request);
    Task<MarketMatchResponse?>           MatchMarketAsync(MarketMatchRequest request);
    Task<RiskAlarmResponse?>             GetRiskAlarmsAsync(RiskAlarmRequest request);
}
```

---

## Endpoint Mapping

| C# Method | Python Endpoint | HTTP Method |
|-----------|----------------|-------------|
| `GetHealthAsync()` | `/health` | GET |
| `AnalyzeSkillsAsync()` | `/analyze-skills` | POST |
| `PredictFeasibilityAsync()` | `/predict-feasibility` | POST |
| `CheckSimilarityAsync()` | `/check-similarity` | POST |
| `MatchMarketAsync()` | `/match-market` | POST |
| `GetRiskAlarmsAsync()` | `/risk-alarms` | POST |

---

## DTO Contracts

### Health
```
C# Request:  (none — GET)
C# Response: AiHealthResponse { Status: string }

Python:
GET /health → { "status": "Python AI service running" }
```

### Skill Analysis
```
C# Request:  SkillAnalysisRequest(string[] Skills, string Level)
C# Response: SkillAnalysisResponse { SkillScore, RecommendedLevel, Message }

Python POST /analyze-skills:
  Body: { "skills": [...], "level": "intermediate" }
  Returns: { "skillScore": 82, "recommendedLevel": "intermediate", "message": "..." }
```

### Feasibility Prediction
```
C# Request:  FeasibilityPredictionRequest(
               int SkillMatchScore, int MissingSkillsCount, int TimelineWeeks,
               int ComplexityScore, int TeamSize,
               bool AiRequired, bool DatasetRequired, bool DeploymentRequired,
               int AcademicValue, int MarketValue)
C# Response: FeasibilityPredictionResponse { FeasibilityScore, RiskLevel, Explanation }

Python POST /predict-feasibility:
  Body: { "skill_match_score": 70, "missing_skills_count": 1, ... }
  Returns: { "feasibility_score": 72, "risk_level": "medium", "explanation": "..." }
```

### Similarity Check
```
C# Request:  SimilarityCheckRequest(string Title, string Description)
C# Response: SimilarityCheckResponse { SimilarityScore, OriginalityScore, IsOriginal }

Python POST /check-similarity:
  Body: { "title": "...", "description": "..." }
  Returns: { "similarity_score": 35, "originality_score": 65, "is_original": true }
```

### Market Match
```
C# Request:  MarketMatchRequest(string IdeaTitle, string IdeaDescription, string Domain)
C# Response: MarketMatchResponse { MarketRelevanceScore, BestMatchSector, MarketInsight }

Python POST /match-market:
  Body: { "idea_title": "...", "idea_description": "...", "domain": "..." }
  Returns: { "market_relevance_score": 88, "best_match_sector": "Healthcare", ... }
```

### Risk Alarms
```
C# Request:  RiskAlarmRequest(int SkillMatchScore, int MissingSkillsCount,
               int TimelineWeeks, int ComplexityScore,
               bool DatasetRequired, bool AiRequired)
C# Response: RiskAlarmResponse { Alarms: List<RiskAlarmItem>, OverallRisk }

Python POST /risk-alarms:
  Body: { "skill_match_score": 45, "missing_skills_count": 4, ... }
  Returns: { "alarms": [...], "overall_risk": "high" }
```

---

## Error Handling

`AiServiceClient` handles all errors gracefully:

- **Timeout:** 10-second timeout per request
- **Connection refused:** Returns `null`, logs the error — app continues without AI features
- **Non-2xx response:** Logs warning, returns `null`
- **Deserialization error:** Returns `null`

Page models and controllers always check for `null` response and show a friendly message instead of crashing.

---

## Adding a New AI Endpoint

1. Add the new method to `IAiServiceClient.cs`
2. Implement it in `AiServiceClient.cs`
3. Add the request/response DTOs to `FYPilot.Application/DTOs/`
4. Add the Python endpoint to the appropriate router in `services/FYPilot.AI/app/routers/`
5. Call it from the relevant Razor Page or controller
