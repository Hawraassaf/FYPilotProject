# FYPilot

**AI-powered Final Year Project planning and management platform for Lebanese CS students.**

Built with Clean Architecture: ASP.NET Core Razor Pages (frontend) + REST API + Python FastAPI AI service + PostgreSQL.

---

## Quick Start

**Terminal 1 — Python AI service:**
```bash
cd services/FYPilot.AI
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac/Linux
pip install -r requirements.txt
python run.py
```

**Terminal 2 — Web app:**
```bash
cd src/FYPilot.Web
dotnet restore
dotnet run --urls http://localhost:5000
```

Open: **http://localhost:5000**
System test: **http://localhost:5000/SystemTest**

**Optional — REST API (Swagger):**
```bash
cd src/FYPilot.Api
dotnet run --urls http://localhost:8080
# Swagger: http://localhost:8080/swagger
```

> Or use the PowerShell start script: `.\scripts\start-all.ps1`

---

## Project Structure

```
FYPilot/
├── FYPilot.sln                          ← Open in Visual Studio 2022
├── src/
│   ├── FYPilot.Domain/                  Business entities
│   ├── FYPilot.Application/             DTOs + interfaces
│   ├── FYPilot.Infrastructure/          EF Core, DB, AI client
│   ├── FYPilot.Api/                     REST API (port 8080) — optional
│   ├── FYPilot.Web/                     Razor Pages web app (port 5000) ← MAIN APP
│   ├── FYPilot.AppHost/                 Startup orchestration
│   └── FYPilot.ServiceDefaults/         Shared defaults
├── services/
│   └── FYPilot.AI/                      Python FastAPI AI service (port 8000)
├── docs/                                Setup guides and architecture docs
├── scripts/
│   └── start-all.ps1                    PowerShell start script
├── .env.example                         Copy to .env and fill in your values
└── README.md
```

**Important:**
- `FYPilot.Web` is the main app that users interact with — Razor Pages, no React/Node required.
- `FYPilot.Api` is optional — for external/programmatic access via JWT.
- Students, supervisors, and admins all log in through `FYPilot.Web`.
- Python AI must be running for AI features (idea generation, roadmap, etc.).
- PostgreSQL must be running for all database features.

---

## Prerequisites

- [.NET 8 SDK](https://dotnet.microsoft.com/download)
- [Python 3.10+](https://www.python.org/downloads/)
- [PostgreSQL 14+](https://www.postgresql.org/download/)

---

## Database Setup

Default development configuration:

| Setting | Value |
|---------|-------|
| Host | localhost |
| Port | 5432 |
| Database | fyp_db |
| Username | postgres |
| Password | 123456 |

Create the database in psql or pgAdmin:
```sql
CREATE DATABASE fyp_db;
```

The app auto-creates tables and seeds demo data on first run — no SQL scripts needed.

**Environment variables (set before running):**

```bash
# Option A — full connection string
DATABASE_URL=Host=localhost;Port=5432;Database=fyp_db;Username=postgres;Password=123456;SSL Mode=Disable;Trust Server Certificate=true;

# Option B — individual variables
PGHOST=localhost
PGPORT=5432
PGDATABASE=fyp_db
PGUSER=postgres
PGPASSWORD=123456

AI_SERVICE_URL=http://localhost:8000
```

See `.env.example` for the full list.

---

## Demo Accounts (password: `password123`)

| Role | Email |
|------|-------|
| Student | student@fyp.com |
| Supervisor | supervisor@fyp.com |
| Admin | admin@fyp.com |

---

## Architecture

```
Browser
  │
  ▼ HTTP :5000
FYPilot.Web (Razor Pages — cookie auth)
  │                    │
  ▼ EF Core            ▼ HTTP
PostgreSQL        FYPilot.AI (Python FastAPI :8000)
  (fyp_db)              /health, /analyze-skills,
                        /predict-feasibility,
                        /check-similarity,
                        /match-market, /risk-alarms

FYPilot.Api (REST API :8080 — JWT auth — optional)
  │                    │
  ▼ EF Core            ▼ HTTP
PostgreSQL        FYPilot.AI
```

See `docs/ARCHITECTURE.md` for full details.

---

## Visual Studio

1. Open `FYPilot.sln`
2. Set **FYPilot.Web** as Startup Project
3. Set environment variables (see `.env.example`)
4. Start the Python AI service in a terminal (see Quick Start above)
5. Press F5

See `docs/VISUAL_STUDIO_SETUP.md` for full step-by-step guide.

---

## Docs

| File | Description |
|------|-------------|
| [docs/VISUAL_STUDIO_SETUP.md](docs/VISUAL_STUDIO_SETUP.md) | Opening and running in Visual Studio |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Clean Architecture layers explained |
| [docs/AI_ENGINE.md](docs/AI_ENGINE.md) | Python AI service endpoints and ML services |
| [docs/CONNECTION_CONTRACTS.md](docs/CONNECTION_CONTRACTS.md) | .NET ↔ Python API contracts |
| [docs/DATABASE_SETUP.md](docs/DATABASE_SETUP.md) | PostgreSQL setup and schema |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and fixes |
