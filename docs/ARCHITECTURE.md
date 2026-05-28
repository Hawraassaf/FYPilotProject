# Architecture — FYPilot

## Overview

FYPilot follows **Clean Architecture** principles across 7 .NET projects, plus a separate Python FastAPI AI microservice.

```
┌───────────────────────────────────────────────────────────┐
│                      Browser / User                        │
└─────────────────────────┬─────────────────────────────────┘
                          │ HTTP :5000
                          ▼
┌───────────────────────────────────────────────────────────┐
│              FYPilot.Web  (Razor Pages)                    │
│  • ASP.NET Core 8                                         │
│  • Cookie-based authentication                            │
│  • Bootstrap 5 responsive UI                             │
│  • Server-rendered pages (no React/Node needed)           │
│  • Talks to DB directly via EF Core                       │
│  • Calls AI via IAiServiceClient interface                │
└─────────┬──────────────────────────┬──────────────────────┘
          │ EF Core                  │ HTTP (IAiServiceClient)
          ▼                          ▼
┌──────────────────┐   ┌─────────────────────────────────────┐
│   PostgreSQL     │   │    FYPilot.AI  (Python FastAPI)      │
│   fyp_db         │   │    Port: 8000                        │
│                  │   │    /health, /analyze-skills          │
│  All app data    │   │    /predict-feasibility              │
│  stored here     │   │    /check-similarity                 │
└──────────────────┘   │    /match-market, /risk-alarms       │
                        └─────────────────────────────────────┘

┌───────────────────────────────────────────────────────────┐
│              FYPilot.Api  (REST API — optional)            │
│  • Port: 8080                                             │
│  • JWT authentication                                     │
│  • Swagger UI at /swagger                                 │
│  • For external/programmatic access                       │
│  • Same DB and AI service as FYPilot.Web                 │
└───────────────────────────────────────────────────────────┘
```

---

## Clean Architecture Layers

### Layer 1 — Domain (innermost, no dependencies)
**Project:** `FYPilot.Domain`

Contains only C# entity classes — the core business objects of the system. No framework dependencies.

Key entities:
- `User` — Student, Supervisor, Admin (role-based)
- `ProjectIdea` — Student project proposals
- `StudentSkill` — Skill ratings per student
- `ProjectRoadmap` + `RoadmapPhase` — AI-generated timelines
- `FeasibilityReport` — AI feasibility analysis
- `SupervisorEvaluation` — Supervisor scores and feedback
- `Milestone`, `MeetingRequest`, `ProgressUpdate`, `Notification`

---

### Layer 2 — Application
**Project:** `FYPilot.Application`

Defines **what** the system can do without specifying **how**.

- `Interfaces/IAiServiceClient.cs` — Contract for calling the Python AI service
- `Interfaces/ITokenService.cs` — Contract for JWT token creation/validation
- `DTOs/` — Data Transfer Objects for all request/response shapes

No infrastructure or framework code here.

---

### Layer 3 — Infrastructure
**Project:** `FYPilot.Infrastructure`

Implements the Application interfaces. Handles all external systems.

- `Data/ApplicationDbContext.cs` — EF Core database context
- `Data/DataSeeder.cs` — Seeds demo accounts on first run
- `Services/AiServiceClient.cs` — HTTP client calling Python AI service
- `Services/IdeaGenerator.cs` — AI idea generation logic
- `Services/RoadmapGenerator.cs` — AI roadmap generation logic
- `Services/FeasibilityAnalyzer.cs` — AI feasibility analysis
- `Services/SimilarityChecker.cs` — AI similarity detection
- `Services/AiMentor.cs` — AI mentor chat
- `Services/TokenService.cs` — JWT token implementation

---

### Layer 4 — Presentation (outermost)
Two presentation projects consume Infrastructure:

**FYPilot.Web** (primary):
- Razor Pages `.cshtml` + `.cshtml.cs` page models
- Cookie authentication, role-based page access
- Bootstrap 5 layout with offcanvas sidebar

**FYPilot.Api** (secondary/optional):
- REST controllers with JSON responses
- JWT Bearer authentication
- Swagger UI

---

## Authentication

| App | Method | Details |
|-----|--------|---------|
| FYPilot.Web | Cookie Auth | Session cookie, 8-hour expiry, sliding window |
| FYPilot.Api | JWT Bearer | Token in Authorization header |

Roles: `student`, `supervisor`, `admin`

---

## Dependency Flow

```
FYPilot.Domain          ← no dependencies
    ▲
FYPilot.Application     ← depends on Domain
    ▲
FYPilot.Infrastructure  ← depends on Application + Domain
    ▲               ▲
FYPilot.Web     FYPilot.Api

FYPilot.ServiceDefaults ← shared config (used by Web + Api)
FYPilot.AppHost         ← orchestration only
```

The dependency rule is always inward — outer layers depend on inner layers, never the reverse.
