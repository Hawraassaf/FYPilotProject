# Database Setup — FYPilot

## Technology

| Item | Value |
|------|-------|
| Database | PostgreSQL 14+ |
| ORM | Entity Framework Core 8 |
| Driver | Npgsql |
| Default DB name | fyp_db |

---

## Local Development Setup

### Step 1 — Install PostgreSQL

Download from: https://www.postgresql.org/download/

During installation, set a password for the `postgres` user. The default dev config uses `123456`.

### Step 2 — Create the Database

Open psql, pgAdmin, or any PostgreSQL client and run:

```sql
CREATE DATABASE fyp_db;
```

### Step 3 — Set Connection Variables

**Option A — Full connection string (highest priority):**
```
DATABASE_URL=Host=localhost;Port=5432;Database=fyp_db;Username=postgres;Password=123456;SSL Mode=Disable;Trust Server Certificate=true;
```

**Option B — Individual variables:**
```
PGHOST=localhost
PGPORT=5432
PGDATABASE=fyp_db
PGUSER=postgres
PGPASSWORD=123456
```

Set these as system environment variables, in `.env`, or in Visual Studio's `launchSettings.json`.

### Step 4 — Let the App Create the Schema

The app calls `db.Database.EnsureCreated()` on startup, which creates all tables automatically from the EF Core entity classes.

No manual SQL migrations needed for a fresh installation.

---

## Connection String Priority

The app resolves the connection string in this order:

1. `DATABASE_URL` environment variable (if set, used as-is)
2. Individual `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` variables
3. Default values: `localhost:5432 / fyp_db / postgres / 123456`

This logic is in `BuildConnectionString()` in both:
- `src/FYPilot.Web/Program.cs`
- `src/FYPilot.Api/Program.cs`

---

## Key Tables

| Table | Description |
|-------|-------------|
| `Users` | All users — student, supervisor, admin roles |
| `StudentProfiles` | Extended student information |
| `SupervisorProfiles` | Extended supervisor information |
| `ProjectIdeas` | Student project idea submissions |
| `StudentSkills` | Skill ratings per student (1–5 per skill) |
| `ProjectRoadmaps` | AI-generated project roadmaps |
| `RoadmapPhases` | Individual phases within a roadmap |
| `FeasibilityReports` | AI feasibility analysis results |
| `SupervisorEvaluations` | Supervisor scores and feedback |
| `ChatMessages` | AI mentor chat history |
| `Milestones` | Project milestones and deadlines |
| `ProjectDocuments` | Generated or uploaded documents |
| `MeetingRequests` | Student-supervisor meeting scheduling |
| `ProgressUpdates` | Weekly progress logs |
| `Notifications` | In-app notifications |

---

## Demo Data

On first run, `DataSeeder.cs` creates three demo accounts:

| Role | Email | Password |
|------|-------|----------|
| Student | student@fyp.com | password123 |
| Supervisor | supervisor@fyp.com | password123 |
| Admin | admin@fyp.com | password123 |

Sample project ideas, skills, and roadmap data are also seeded for the demo student.

---

## Viewing the Data

Use pgAdmin (GUI) or psql (command line):

```bash
psql -U postgres -d fyp_db

# List tables
\dt

# Count users
SELECT role, COUNT(*) FROM "Users" GROUP BY role;

# View project ideas
SELECT title, status FROM "ProjectIdeas" LIMIT 10;
```

---

## Resetting the Database

To start fresh (drops all data):

```sql
DROP DATABASE fyp_db;
CREATE DATABASE fyp_db;
```

Then restart the app — it will recreate all tables and re-seed demo data.

---

## EF Core Context Location

`src/FYPilot.Infrastructure/Data/ApplicationDbContext.cs`

All DbSet declarations are here. To add a new table:
1. Add the entity class to `FYPilot.Domain/Entities/`
2. Add `DbSet<EntityName> EntityNames { get; set; }` to `ApplicationDbContext.cs`
3. Restart the app — `EnsureCreated()` adds the new table
