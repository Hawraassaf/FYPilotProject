# Visual Studio Setup Guide

## Prerequisites

| Tool | Version | Download |
|------|---------|----------|
| Visual Studio | 2022 (any edition) | https://visualstudio.microsoft.com |
| .NET SDK | 8.0+ | https://dotnet.microsoft.com/download |
| Python | 3.10+ | https://python.org |
| PostgreSQL | 14+ | https://postgresql.org |

During Visual Studio installation, select the workload:
**ASP.NET and web development**

---

## Step 1 — Create the Database

Open pgAdmin or psql and run:

```sql
CREATE DATABASE fyp_db;
```

---

## Step 2 — Set Environment Variables

Set these as **System Environment Variables** (Control Panel → System → Advanced → Environment Variables):

| Variable | Value |
|----------|-------|
| `PGHOST` | `localhost` |
| `PGPORT` | `5432` |
| `PGDATABASE` | `fyp_db` |
| `PGUSER` | `postgres` |
| `PGPASSWORD` | `123456` |
| `AI_SERVICE_URL` | `http://localhost:8000` |
| `ASPNETCORE_ENVIRONMENT` | `Development` |

> Restart Visual Studio after setting environment variables.

Alternatively, copy `.env.example` to `.env` and set the values — or set them in `launchSettings.json` under the project.

---

## Step 3 — Open the Solution

1. Double-click **`FYPilot.sln`** in the project root
2. Visual Studio opens with all 7 projects visible in Solution Explorer:
   - FYPilot.Domain
   - FYPilot.Application
   - FYPilot.Infrastructure
   - FYPilot.Api
   - FYPilot.Web ← **Startup project**
   - FYPilot.AppHost
   - FYPilot.ServiceDefaults

---

## Step 4 — Set Startup Project

Right-click **FYPilot.Web** → **Set as Startup Project**

---

## Step 5 — Start the Python AI Service

Open a terminal (PowerShell or Command Prompt) and run:

```powershell
cd services\FYPilot.AI
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

The AI service starts on **http://localhost:8000**

---

## Step 6 — Run the App

Press **F5** in Visual Studio (or click the green play button).

The browser opens automatically at **http://localhost:5000**

---

## Step 7 — Verify Everything Works

Navigate to **http://localhost:5000/SystemTest** and click **Run Tests**.
All checks should be green.

---

## Demo Accounts

| Role | Email | Password |
|------|-------|----------|
| Student | student@fyp.com | password123 |
| Supervisor | supervisor@fyp.com | password123 |
| Admin | admin@fyp.com | password123 |

Demo data is seeded automatically on first run — no manual setup needed.

---

## Notes

- The database tables are created automatically using `EnsureCreated()` on startup
- You do **not** need Node.js or npm — the frontend is Razor Pages (server-rendered)
- The REST API (`FYPilot.Api`) is optional; the main app is `FYPilot.Web`
- If AI features show errors, ensure the Python service is running on port 8000
