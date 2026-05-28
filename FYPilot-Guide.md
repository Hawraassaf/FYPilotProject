# FYPilot — Complete System Guide

---

## 1. What Is FYPilot?

FYPilot is an **AI-powered Final Year Project (FYP) planning and management platform** built specifically for Lebanese Computer Science students. It helps students generate project ideas based on their skills, plan a project roadmap, track progress, receive supervisor evaluations, and get AI-driven mentoring — all in one platform.

Three types of users exist:
- **Students** — plan, generate ideas, track their FYP
- **Supervisors** — review ideas, evaluate students
- **Admins** — manage users, projects, and platform analytics

---

## 2. Technologies Used

### Backend — .NET 8 (C#)
| Technology | Purpose |
|---|---|
| ASP.NET Core 8 | Web framework for both the API and the web app |
| Razor Pages | Server-rendered HTML pages (the frontend) |
| Entity Framework Core 8 | Database access and migrations (ORM) |
| Npgsql | PostgreSQL driver for EF Core |
| BCrypt.Net | Password hashing |
| Swashbuckle (Swagger) | Auto-generated API documentation |
| Cookie Authentication | Session management for logged-in users |

### AI / ML Service — Python
| Technology | Purpose |
|---|---|
| FastAPI | Web framework for the AI microservice |
| Uvicorn | ASGI server running FastAPI |
| Pydantic v2 | Request/response validation |
| scikit-learn | Machine learning models (grade prediction, skill gap, risk) |
| sentence-transformers | Semantic similarity checking between project ideas |
| OpenAI SDK | LLM integration for mentor chat and suggestions |
| Pandas / NumPy | Data processing and analysis |
| SQLAlchemy | Database access from Python |
| Plotly / Matplotlib | Data visualisation |

### Database
| Technology | Purpose |
|---|---|
| PostgreSQL | Primary relational database (shared by .NET and Python) |

### Frontend (built into FYPilot.Web)
| Technology | Purpose |
|---|---|
| Razor Pages (.cshtml) | Server-side HTML rendering |
| Bootstrap 5 | Responsive layout and UI components |
| Bootstrap Icons | Icon library |
| Vanilla JavaScript | Interactive UI (charts, dynamic updates) |
| Chart.js | Progress charts and analytics |

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    User's Browser                        │
└───────────────────────┬─────────────────────────────────┘
                        │  HTTP (port 5000)
                        ▼
┌─────────────────────────────────────────────────────────┐
│              FYPilot.Web  (Razor Pages)                  │
│  • Renders HTML pages for all 3 user roles              │
│  • Cookie-based login/session                           │
│  • Talks directly to DB via EF Core                     │
│  • Calls AI service for smart features                  │
└──────────┬──────────────────────────┬───────────────────┘
           │ Direct DB access          │ HTTP calls
           ▼                           ▼
┌──────────────────┐      ┌─────────────────────────────┐
│    PostgreSQL     │      │   FYPilot.AI  (Python)       │
│                  │      │  • Idea generation           │
│  All entities:   │      │  • Skill gap analysis        │
│  Users, Ideas,   │      │  • Roadmap generation        │
│  Skills, Roadmap,│      │  • Risk engine               │
│  Evaluations,    │      │  • Feasibility analysis      │
│  Milestones…     │      │  • Similarity checker        │
└──────────────────┘      │  • AI mentor chat            │
                          │  • Market analysis           │
                          └─────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│           FYPilot.Api  (REST API — port 8080)            │
│  • Separate API server (JWT auth)                       │
│  • For external or programmatic access                  │
│  • Swagger UI available                                 │
│  • Also talks to DB and AI service                      │
└─────────────────────────────────────────────────────────┘
```

**Key design decisions:**
- The **web app (Razor Pages)** is the primary interface — users never interact with the API directly
- **Cookie authentication** is used for the web app (no tokens needed in the browser)
- The **REST API** (port 8080) exists for external/programmatic access and uses JWT tokens
- Both the web app and the API share the same database and AI service

---

## 4. Clean Architecture — The 7 Projects

The .NET solution (`FYPilot.sln`) follows Clean Architecture — each layer only depends on the layer below it, never above.

```
FYPilot.Domain          ← No dependencies (pure business entities)
     ▲
FYPilot.Application     ← Depends on Domain (DTOs + interfaces)
     ▲
FYPilot.Infrastructure  ← Depends on Application + Domain (DB + AI client)
     ▲                ▲
FYPilot.Api         FYPilot.Web    ← Both depend on Infrastructure
     
FYPilot.AppHost         ← Orchestration only
FYPilot.ServiceDefaults ← Shared defaults (logging, health checks)
```

### Project 1 — FYPilot.Domain
**Language:** C#  
**Purpose:** The core of the system. Contains all business entities (database tables) with no external dependencies.

**Key files:**
- `Entities/User.cs` — User with roles: Student, Supervisor, Admin
- `Entities/FypilotEntities.cs` — ProjectIdea, ProjectRoadmap, RoadmapPhase, StudentSkill, FeasibilityReport, SupervisorEvaluation, ChatMessage, Milestone, ProjectDocument, MeetingRequest, ProgressUpdate, Notification
- `Entities/LegacyEntities.cs` — Earlier entities still in use
- `Entities/Profiles.cs` — StudentProfile, SupervisorProfile

---

### Project 2 — FYPilot.Application
**Language:** C#  
**Purpose:** Defines what the system can do (interfaces) and how data moves between layers (DTOs). No implementation details.

**Key files:**
- `Interfaces/IAiServiceClient.cs` — Contract for calling the Python AI service
- `Interfaces/ITokenService.cs` — Contract for JWT token creation
- `DTOs/FypilotDTOs.cs` — Request/response shapes for ideas, skills, roadmaps
- `DTOs/AiExtendedDTOs.cs` — DTOs for AI features (feasibility, risk, chat)
- `DTOs/DashboardDTOs.cs` — Aggregated data for dashboard pages
- `DTOs/LegacyDTOs.cs` — Backward-compatible DTOs

---

### Project 3 — FYPilot.Infrastructure
**Language:** C#  
**Purpose:** The implementation layer. Handles the database, password hashing, JWT tokens, and all communication with the Python AI service.

**Key files:**
- `Data/ApplicationDbContext.cs` — EF Core database context (all DbSets)
- `Data/DataSeeder.cs` — Seeds demo accounts and sample data on startup
- `Services/AiServiceClient.cs` — HTTP client that calls Python FastAPI endpoints
- `Services/IdeaGenerator.cs` — Calls AI to generate project ideas based on skills
- `Services/RoadmapGenerator.cs` — Calls AI to build a week-by-week roadmap
- `Services/FeasibilityAnalyzer.cs` — Calls AI for feasibility scoring
- `Services/SimilarityChecker.cs` — Calls AI to detect duplicate/similar ideas
- `Services/AiMentor.cs` — Calls AI for chat-based mentoring
- `Services/DataScienceService.cs` — Risk analysis, grade prediction
- `Services/TokenService.cs` — Creates and validates JWT tokens
- `Services/PlanGenerator.cs` — Project plan generation
- `Services/DocumentationGenerator.cs` — Auto-documentation features
- `Services/PresentationGenerator.cs` — Presentation content generation

---

### Project 4 — FYPilot.Api
**Language:** C#  
**Purpose:** A full REST API exposing all features as JSON endpoints. Intended for external integrations or programmatic access. Uses JWT authentication.

**Controllers and their endpoints:**

| Controller | Endpoints |
|---|---|
| AuthController | POST /api/auth/login, POST /api/auth/register, GET /api/auth/me |
| IdeasController | GET/POST /api/ideas, PUT/DELETE /api/ideas/{id}, POST /api/ideas/generate |
| SkillsController | GET/POST /api/skills/bulk |
| RoadmapController | POST /api/roadmap/generate, GET /api/roadmap/{id} |
| FeedbackController | GET/POST /api/feedback, POST /api/feedback/supervisor |
| ProjectsController | GET/POST /api/projects, PUT /api/projects/{id} |
| TasksController | GET/POST /api/tasks, PUT /api/tasks/{id} |
| MilestonesController | GET/POST /api/milestones |
| DashboardController | GET /api/dashboard/student, GET /api/dashboard/supervisor |
| UsersController | GET /api/users, GET /api/users/{id} |
| AdminController | GET /api/admin/stats, GET /api/admin/users |
| SupervisorEvalController | GET/POST /api/evaluations |
| AiController | POST /api/ai/chat, POST /api/ai/risk, POST /api/ai/suggestions |
| FeasibilityController | POST /api/feasibility/analyze |
| ChatController | POST /api/chat/message |
| MarketController | GET /api/market/needs |
| DataScienceController | POST /api/datascience/predict |
| ChallengesController | GET /api/challenges |
| SystemController | GET /api/system/health, GET /api/system/ai-health, GET /api/system/database-health |

**Swagger UI:** http://localhost:8080/swagger

---

### Project 5 — FYPilot.Web
**Language:** C# (Razor Pages) + HTML/CSS/JavaScript  
**Purpose:** The main web application that users interact with. Server-rendered pages with Bootstrap 5 UI. Cookie-based authentication.

**Layout:**
- Responsive sidebar (offcanvas drawer on mobile, pinned on desktop)
- Bootstrap 5 with Bootstrap Icons
- Role-based navigation (different sidebar items per role)

**All Pages:**

#### Account (public — no login needed)
| Page | URL | Description |
|---|---|---|
| Login | /Account/Login | Email + password login form |
| Register | /Account/Register | Create a new student account |
| Logout | /Account/Logout | Signs out and redirects to login |

#### Student Pages (requires Student role)
| Page | URL | Description |
|---|---|---|
| Dashboard | /Student/Dashboard | Overview: idea status, progress ring, recent activity |
| Skill Assessment | /Student/SkillAssessment | Rate yourself on CS skills (1–5 stars) — saves to DB |
| Idea Generator | /Student/IdeaGenerator | AI generates 3–5 project ideas based on your skills |
| Idea Comparison | /Student/IdeaComparison | Side-by-side comparison of saved project ideas |
| Project Details | /Student/ProjectDetails | Full view of your active project with tasks |
| Project DNA | /Student/ProjectDNA | Visual breakdown of project characteristics |
| Roadmap | /Student/Roadmap | AI-generated week-by-week project roadmap |
| Scope Optimizer | /Student/ScopeOptimizer | AI refines and narrows your project scope |
| Defense Simulator | /Student/DefenseSimulator | Practice Q&A for your FYP defense |
| Mentor Chat | /Student/MentorChat | Chat with an AI mentor about your project |
| Feedback | /Student/Feedback | Your own self-feedback and progress notes |
| Supervisor Feedback | /Student/SupervisorFeedback | Evaluations submitted by your supervisor |

#### Supervisor Pages (requires Supervisor role)
| Page | URL | Description |
|---|---|---|
| Dashboard | /Supervisor/Dashboard | Overview of assigned students and activity |
| Idea Review | /Supervisor/IdeaReview | Review and approve/reject student project ideas |
| Evaluations | /Supervisor/Evaluations | Submit evaluations with score, status, and comments |

#### Admin Pages (requires Admin role)
| Page | URL | Description |
|---|---|---|
| Dashboard | /Admin/Dashboard | Platform-wide statistics |
| Users | /Admin/Users | View and manage all user accounts |
| Projects | /Admin/Projects | View all active projects |
| Analytics | /Admin/Analytics | Charts and reports |
| Market Needs | /Admin/MarketNeeds | Industry demand data |

#### System
| Page | URL | Description |
|---|---|---|
| System Test | /SystemTest | Runs health checks on DB, API, and AI service |
| Index | / | Redirects to dashboard based on role |

---

### Project 6 — FYPilot.AppHost
**Language:** C#  
**Purpose:** Orchestration project — documents how all services connect and start up (similar to .NET Aspire). Not a running service itself.

---

### Project 7 — FYPilot.ServiceDefaults
**Language:** C#  
**Purpose:** Shared configuration applied to all services — logging setup, health check endpoints, telemetry defaults.

---

## 5. Python AI Service — FYPilot.AI

**Framework:** FastAPI  
**Port:** 8000  
**Entry point:** `services/FYPilot.AI/run.py` → starts Uvicorn

### Routers (API route groups)

| Router | Endpoints | Description |
|---|---|---|
| health.py | GET /health | Confirms AI service is alive |
| intelligence.py | POST /generate-ideas, POST /generate-roadmap, POST /mentor-chat | Core AI generation features |
| feasibility.py | POST /feasibility/analyze | Scores a project idea for viability |
| risk.py | POST /risk/analyze | Identifies risks in a project plan |
| analytics.py | POST /analytics/predict-grade, POST /analytics/burndown | Grade prediction and burndown charts |
| similarity.py | POST /similarity/check | Detects if two project ideas are too similar |
| market.py | GET /market/needs | Returns current industry demand topics |

### ML Services (internal)

| Service | Description |
|---|---|
| skill_gap_ml.py | Identifies gaps between student skills and project requirements |
| grade_predictor.py | Predicts likely final grade based on progress data |
| risk_engine.py | Scores and categorises project risks |
| roadmap_generator.py | Builds a phased project roadmap |
| similarity_checker.py | Semantic similarity using sentence-transformers |
| supervisor_matcher.py | Matches students with suitable supervisors |
| burndown_engine.py | Generates burndown chart data |
| anomaly_detector.py | Flags unusual patterns in student progress |
| analytics_engine.py | Aggregates analytics data |

---

## 6. Database — PostgreSQL

Connection is set via the `DATABASE_URL` environment variable.

### Key Tables (Entities)

| Table | Description |
|---|---|
| Users | All users: students, supervisors, admins |
| StudentProfiles | Extended info for students |
| SupervisorProfiles | Extended info for supervisors |
| ProjectIdeas | Student project idea submissions |
| StudentSkills | Student skill ratings (1–5 per skill) |
| ProjectRoadmaps | AI-generated project roadmaps |
| RoadmapPhases | Individual phases within a roadmap |
| FeasibilityReports | AI feasibility analysis results |
| SupervisorEvaluations | Supervisor scores and feedback per student |
| ChatMessages | Mentor chat history |
| Milestones | Project milestones and deadlines |
| ProjectDocuments | Uploaded/generated documents |
| MeetingRequests | Student-supervisor meeting requests |
| ProgressUpdates | Weekly progress logs |
| Notifications | In-app notifications |

**Seeded demo accounts (created automatically on first run):**

| Role | Email | Password |
|---|---|---|
| Student | student@fyp.com | password123 |
| Supervisor | supervisor@fyp.com | password123 |
| Admin | admin@fyp.com | password123 |

---

## 7. How Data Flows — A Complete Example

**Scenario: Student generates project ideas**

```
1. Student opens /Student/IdeaGenerator in browser

2. Page loads their saved skills from PostgreSQL
   (FYPilot.Web → ApplicationDbContext → StudentSkills table)

3. Student clicks "Generate Ideas"

4. Page posts to its own handler (OnPostAsync)

5. Handler calls IdeaGenerator.Generate(skills)
   (FYPilot.Infrastructure/Services/IdeaGenerator.cs)

6. IdeaGenerator sends HTTP POST to Python AI service:
   POST http://localhost:8000/generate-ideas
   Body: { skills: [...], preferences: {...} }

7. Python service processes the request:
   - Runs skill_gap_ml to identify what kind of project fits
   - Uses OpenAI SDK to generate 3–5 tailored ideas
   - Returns structured JSON

8. IdeaGenerator maps the JSON to List<ProjectIdea>

9. Ideas are saved to PostgreSQL
   (ProjectIdeas table)

10. Page re-renders showing the generated ideas
```

---

## 8. Running Locally — Step by Step

### Prerequisites
- [.NET 8 SDK](https://dotnet.microsoft.com/download)
- [Python 3.10+](https://python.org)
- [PostgreSQL](https://postgresql.org) running locally

### Step 1 — Create the database

```sql
CREATE DATABASE fyp;
```

### Step 2 — Set the connection string

**Windows:**
```cmd
set DATABASE_URL=Host=localhost;Database=fyp;Username=postgres;Password=yourpassword
```

**Mac / Linux:**
```bash
export DATABASE_URL="Host=localhost;Database=fyp;Username=postgres;Password=yourpassword"
```

> The app will auto-migrate and seed demo data on first run — no manual SQL needed.

### Step 3 — Start the Python AI service (Terminal 1)

```bash
cd services/FYPilot.AI

# Create virtual environment
python -m venv .venv

# Activate — Windows:
.venv\Scripts\activate
# Activate — Mac/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the service
python run.py
```

AI service runs on **http://localhost:8000**

### Step 4 — Start the web app (Terminal 2)

```bash
dotnet restore FYPilot.sln
dotnet run --project src/FYPilot.Web/FYPilot.Web.csproj --urls http://localhost:5000
```

Open **http://localhost:5000** in your browser.

### Step 5 (optional) — Start the REST API (Terminal 3)

Only needed if you want to use the API or Swagger:

```bash
dotnet run --project src/FYPilot.Api/FYPilot.Api.csproj --urls http://localhost:8080
```

Swagger: **http://localhost:8080/swagger**

### Step 6 — Verify everything works

Go to **http://localhost:5000/SystemTest** and click **Run All Tests** — all health checks should be green.

---

## 9. Opening in Visual Studio

1. Double-click **`FYPilot.sln`** — Visual Studio opens all 7 projects
2. Set **FYPilot.Web** as the Startup Project (right-click → Set as Startup Project)
3. Set `DATABASE_URL` in your system environment variables
4. Press **F5** — the web app starts and the browser opens automatically

> To run the Python AI service alongside it, open a terminal in VS and follow Step 3 above.

---

## 10. File Structure Reference

```
FYPilot/
├── FYPilot.sln                          ← Open this in Visual Studio
│
├── src/
│   ├── FYPilot.Domain/
│   │   └── Entities/
│   │       ├── User.cs                  ← User entity with roles
│   │       ├── FypilotEntities.cs       ← All main entities
│   │       ├── LegacyEntities.cs        ← Older entities
│   │       └── Profiles.cs              ← Student/Supervisor profiles
│   │
│   ├── FYPilot.Application/
│   │   ├── Interfaces/
│   │   │   ├── IAiServiceClient.cs      ← AI service contract
│   │   │   └── ITokenService.cs         ← JWT contract
│   │   └── DTOs/
│   │       ├── FypilotDTOs.cs
│   │       ├── AiExtendedDTOs.cs
│   │       ├── DashboardDTOs.cs
│   │       └── LegacyDTOs.cs
│   │
│   ├── FYPilot.Infrastructure/
│   │   ├── Data/
│   │   │   ├── ApplicationDbContext.cs  ← EF Core DB context
│   │   │   └── DataSeeder.cs            ← Seeds demo accounts
│   │   └── Services/
│   │       ├── AiServiceClient.cs       ← Calls Python service
│   │       ├── IdeaGenerator.cs
│   │       ├── RoadmapGenerator.cs
│   │       ├── FeasibilityAnalyzer.cs
│   │       ├── SimilarityChecker.cs
│   │       ├── AiMentor.cs
│   │       ├── DataScienceService.cs
│   │       └── TokenService.cs
│   │
│   ├── FYPilot.Api/
│   │   ├── Controllers/                 ← 18 REST API controllers
│   │   └── Program.cs                   ← JWT auth, Swagger, DI
│   │
│   ├── FYPilot.Web/
│   │   ├── Pages/
│   │   │   ├── Account/                 ← Login, Register, Logout
│   │   │   ├── Student/                 ← 12 student pages
│   │   │   ├── Supervisor/              ← 3 supervisor pages
│   │   │   ├── Admin/                   ← 5 admin pages
│   │   │   └── Shared/
│   │   │       └── _Layout.cshtml       ← Sidebar + responsive layout
│   │   ├── wwwroot/
│   │   │   └── css/site.css             ← Custom styles
│   │   └── Program.cs                   ← Cookie auth, EF Core, seeding
│   │
│   ├── FYPilot.AppHost/                 ← Orchestration
│   └── FYPilot.ServiceDefaults/         ← Shared logging/health config
│
└── services/
    └── FYPilot.AI/
        ├── app/
        │   ├── main.py                  ← FastAPI entry point
        │   ├── routers/                 ← 7 route groups
        │   ├── services/                ← 9 ML services
        │   └── models/schemas.py        ← Pydantic schemas
        ├── requirements.txt
        └── run.py                       ← Starts Uvicorn server
```
