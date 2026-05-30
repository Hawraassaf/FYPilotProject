---
name: FYPilot Project Instructions
description: |
  Repository-wide instructions for FYPilot development.
  Provides context about the project structure and architectural patterns.
---

# FYPilot Repository Guidelines

## Project Overview

**FYPilot** is a Final Year Project recommendation and analysis platform built with Clean Architecture principles.

- **Type**: Final Year Project (FYP) Assistance Platform
- **Frontend**: .NET 8 ASP.NET Core Razor Pages
- **Backend AI**: Python FastAPI Service
- **Database**: PostgreSQL
- **Deployment**: Modular microservices approach

## Directory Structure

```
FYPilot/
├── src/                              # .NET Application (ASP.NET Core)
│   ├── FYPilot.Domain/              # Business logic & entities
│   ├── FYPilot.Application/         # Use cases & services
│   └── FYPilot.Presentation/        # Razor Pages & API controllers
├── services/
│   └── FYPilot.AI/                  # Python FastAPI Service
│       ├── app/
│       │   ├── agents/              # Intelligent agents (ProjectIdeaAgent, etc.)
│       │   ├── routers/             # FastAPI route handlers
│       │   ├── services/            # Business logic & external integrations
│       │   ├── models/              # Pydantic schemas
│       │   ├── utils/               # Utilities & helpers
│       │   └── main.py              # FastAPI app entry point
│       └── requirements.txt          # Python dependencies
├── docs/                             # Documentation
├── scripts/                          # Utility scripts
└── README.md                         # Project overview
```

## Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Backend Framework | ASP.NET Core | 8.0 |
| Frontend | Razor Pages | - |
| Python AI Service | FastAPI | Latest |
| Database | PostgreSQL | 12+ |
| Local LLM | Ollama | Latest |
| LLM Model | Phi-3 | Local |
| Runtime | Python | 3.10+ |

## Key Patterns

### Clean Architecture
- Separation of concerns
- Testability first
- Business logic independent of frameworks

### Agent-Based Architecture
- Specialized agents for specific tasks
- Deterministic scoring systems
- Safe fallback responses

### Python Service Rules
- No database connections (PostgreSQL managed by .NET)
- Ollama is optional enhancement, not requirement
- All responses must be safe even if external services fail
- Deterministic scores, never LLM-generated values

## Deployment & Running

### Start Python Service
```bash
cd services/FYPilot.AI
python -m uvicorn app.main:app --reload --port 8000
```

### Access Swagger Docs
```
http://localhost:8000/docs
```

### Test .NET Application
```bash
cd src
dotnet run
```

## Important Constraints

✋ **Do Not Change**:
- .NET code without explicit request
- Database schema or migrations
- Existing endpoints
- Razor Pages

✅ **Safe to Modify**:
- Python FastAPI service code
- New Python agents and routers
- Configuration files in `services/FYPilot.AI/`
- Documentation

## Code Style

- **Python**: PEP 8 compliance
- **Type Hints**: Required for all functions
- **Documentation**: Docstrings for all modules and functions
- **Error Handling**: Graceful fallbacks, never crash on external service failures

## Current Focus

**Building ProjectIdeaAgent** - An intelligent agent for generating personalized FYP recommendations based on:
- Student skills and proficiency levels
- Academic major and experience level
- Domain preferences and difficulty targets
- Availability and team constraints
- Lebanese market context

