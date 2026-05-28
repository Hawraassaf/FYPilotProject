using System.Text.Json;
using FYPilot.Domain.Entities;

namespace FYPilot.Infrastructure.Services;

public static class RoadmapGenerator
{
    public static List<RoadmapPhase> Generate(ProjectIdea idea)
    {
        var isAi = idea.Domain.Contains("AI") || idea.Domain.Contains("Data Science");
        var isMobile = idea.Domain.Contains("Mobile");

        return
        [
            new RoadmapPhase
            {
                PhaseNumber = 1, Name = "Research & Problem Understanding",
                Objective = "Understand the problem domain, existing solutions, and define project scope.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    "Review existing solutions (literature review)",
                    "Interview potential users (at least 5)",
                    "Define problem statement clearly",
                    "Identify Lebanese market context and competition",
                    "Write project proposal document"
                }),
                ExpectedOutput = "Problem statement document, competitor analysis, user interviews report",
                ToolsNeeded = "Google Scholar, LinkedIn, Word/LaTeX",
                EstimatedWeeks = 1,
                Dependencies = "None",
                Risks = "Insufficient market research may lead to wrong assumptions",
                SuccessCriteria = "Clear problem statement approved by supervisor"
            },
            new RoadmapPhase
            {
                PhaseNumber = 2, Name = "Requirements Analysis",
                Objective = "Gather and document functional and non-functional requirements.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    "Create user stories",
                    "Define functional requirements",
                    "Define non-functional requirements",
                    "Create use case diagrams",
                    "Get supervisor approval on requirements"
                }),
                ExpectedOutput = "Requirements specification document (SRS), use case diagrams",
                ToolsNeeded = "Lucidchart, draw.io, Word",
                EstimatedWeeks = 1,
                Dependencies = "Phase 1",
                Risks = "Scope creep if requirements are not frozen",
                SuccessCriteria = "SRS document reviewed and approved"
            },
            new RoadmapPhase
            {
                PhaseNumber = 3, Name = "System Design",
                Objective = "Design the system architecture, components, and data flow.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    "Create system architecture diagram",
                    "Design API endpoints (OpenAPI/Swagger)",
                    "Design component/module structure",
                    "Create sequence diagrams for key flows",
                    "Choose technology stack and justify"
                }),
                ExpectedOutput = "Architecture diagram, API spec, component diagram",
                ToolsNeeded = "draw.io, Swagger Editor, Visual Studio",
                EstimatedWeeks = 1,
                Dependencies = "Phase 2",
                Risks = "Poor architecture decisions are costly to reverse",
                SuccessCriteria = "Architecture approved by supervisor"
            },
            new RoadmapPhase
            {
                PhaseNumber = 4, Name = "Database Design",
                Objective = "Design and implement the database schema.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    "Create ER diagram",
                    "Define all tables/entities and relationships",
                    "Write database migration scripts",
                    "Seed sample data",
                    "Test database queries"
                }),
                ExpectedOutput = "ER diagram, SQL migration scripts, seeded database",
                ToolsNeeded = "pgAdmin, DBeaver, Entity Framework Core",
                EstimatedWeeks = 1,
                Dependencies = "Phase 3",
                Risks = "Schema changes later are expensive",
                SuccessCriteria = "All entities created and relationships verified"
            },
            new RoadmapPhase
            {
                PhaseNumber = 5, Name = "Backend Development (C# .NET)",
                Objective = $"Build the ASP.NET Core Web API with all business logic for {idea.Title}.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    "Set up ASP.NET Core project structure",
                    "Implement JWT authentication & authorization",
                    "Implement all API controllers",
                    "Implement service layer and business logic",
                    "Add input validation and error handling",
                    "Write unit tests for services",
                    "Document all API endpoints with Swagger"
                }),
                ExpectedOutput = "Fully functional REST API with Swagger documentation",
                ToolsNeeded = "Visual Studio 2022, Postman, xUnit, Swagger",
                EstimatedWeeks = 3,
                Dependencies = "Phase 4",
                Risks = "Complex business logic bugs; authentication vulnerabilities",
                SuccessCriteria = "All API endpoints tested via Postman with 90%+ coverage"
            },
            new RoadmapPhase
            {
                PhaseNumber = 6, Name = isAi ? "AI/Data Science Development (Python)" : "Data Processing & Analytics (Python)",
                Objective = isAi
                    ? $"Build the Python ML/AI service for {idea.Title}."
                    : "Build data analytics and processing components.",
                TasksJson = isAi
                    ? JsonSerializer.Serialize(new[] {
                        "Set up Python FastAPI service",
                        "Data collection and cleaning",
                        "Feature engineering and preprocessing",
                        "Model training and evaluation",
                        "Build prediction API endpoints",
                        "Integrate with .NET backend"
                    })
                    : JsonSerializer.Serialize(new[] {
                        "Set up Python service",
                        "Implement data processing pipeline",
                        "Build analytics/reporting endpoints",
                        "Integrate with .NET backend"
                    }),
                ExpectedOutput = isAi ? "Trained ML model, Python FastAPI service, integration tests" : "Python analytics service, API integration",
                ToolsNeeded = "Python 3.11+, FastAPI, scikit-learn, pandas, Jupyter",
                EstimatedWeeks = 2,
                Dependencies = "Phase 5",
                Risks = isAi ? "Insufficient or low-quality training data" : "Performance bottlenecks in data processing",
                SuccessCriteria = isAi ? "Model accuracy > 75%, API integration working" : "Analytics service operational and tested"
            },
            new RoadmapPhase
            {
                PhaseNumber = 7, Name = isMobile ? "Mobile Frontend Development (React Native)" : "Frontend Development (React)",
                Objective = $"Build the {(isMobile ? "mobile" : "web")} user interface for {idea.Title}.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    $"Set up {(isMobile ? "React Native" : "React + Vite")} project",
                    "Implement authentication screens",
                    "Build all main pages/screens",
                    "Integrate with backend API",
                    "Add charts and data visualization",
                    "Implement responsive design",
                    "User interface testing"
                }),
                ExpectedOutput = "Complete, responsive frontend application",
                ToolsNeeded = isMobile ? "React Native, Expo, VS Code" : "React, Vite, TailwindCSS, shadcn/ui, Recharts",
                EstimatedWeeks = 3,
                Dependencies = "Phase 5",
                Risks = "UI/UX inconsistencies; browser compatibility issues",
                SuccessCriteria = "All pages functional, responsive on mobile and desktop"
            },
            new RoadmapPhase
            {
                PhaseNumber = 8, Name = "System Integration",
                Objective = "Integrate all components: .NET backend, Python service, and frontend.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    "Connect frontend to .NET API",
                    "Connect .NET API to Python service",
                    "End-to-end integration testing",
                    "Fix integration bugs",
                    "Performance testing and optimization"
                }),
                ExpectedOutput = "Fully integrated system, integration test report",
                ToolsNeeded = "Postman, Chrome DevTools, Python requests",
                EstimatedWeeks = 1,
                Dependencies = "Phase 5, Phase 6, Phase 7",
                Risks = "API contract mismatches; CORS issues; data format inconsistencies",
                SuccessCriteria = "All end-to-end flows working correctly"
            },
            new RoadmapPhase
            {
                PhaseNumber = 9, Name = "Testing",
                Objective = "Comprehensive testing including unit, integration, and user acceptance testing.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    "Write and run unit tests (.NET and Python)",
                    "Write integration tests",
                    "Perform user acceptance testing (UAT)",
                    "Security testing (SQL injection, XSS, CSRF)",
                    "Performance/load testing",
                    "Bug fixing and regression testing"
                }),
                ExpectedOutput = "Test report, bug fix log, security audit report",
                ToolsNeeded = "xUnit, pytest, Playwright, OWASP ZAP",
                EstimatedWeeks = 1,
                Dependencies = "Phase 8",
                Risks = "Undiscovered bugs; security vulnerabilities",
                SuccessCriteria = "90%+ test pass rate; no critical security issues"
            },
            new RoadmapPhase
            {
                PhaseNumber = 10, Name = "Deployment",
                Objective = "Deploy the system to a production-ready environment.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    "Set up cloud environment (Azure/AWS/Railway)",
                    "Configure CI/CD pipeline",
                    "Deploy .NET API",
                    "Deploy Python service",
                    "Deploy frontend",
                    "Configure domain and HTTPS",
                    "Monitor and verify deployment"
                }),
                ExpectedOutput = "Live system accessible via public URL",
                ToolsNeeded = "Docker, GitHub Actions, Azure/Railway/Render",
                EstimatedWeeks = 1,
                Dependencies = "Phase 9",
                Risks = "Environment configuration differences; deployment failures",
                SuccessCriteria = "System live and accessible; all features working in production"
            },
            new RoadmapPhase
            {
                PhaseNumber = 11, Name = "Documentation",
                Objective = "Write complete technical and user documentation.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    "Write technical documentation (architecture, API, DB schema)",
                    "Write user manual",
                    "Write project report (SRS, SDS, test plan)",
                    "Create README files",
                    "Record demo video"
                }),
                ExpectedOutput = "Complete documentation package, user manual, demo video",
                ToolsNeeded = "LaTeX, Word, Swagger, Loom",
                EstimatedWeeks = 1,
                Dependencies = "Phase 10",
                Risks = "Incomplete documentation fails submission requirements",
                SuccessCriteria = "All required documents submitted to supervisor"
            },
            new RoadmapPhase
            {
                PhaseNumber = 12, Name = "Final Presentation",
                Objective = "Prepare and deliver the final FYP presentation and defense.",
                TasksJson = JsonSerializer.Serialize(new[] {
                    "Create presentation slides",
                    "Prepare live demo walkthrough",
                    "Practice presentation (3+ rehearsals)",
                    "Prepare answers for committee questions",
                    "Final submission of all materials"
                }),
                ExpectedOutput = "Presentation slides, live demo, submitted project",
                ToolsNeeded = "PowerPoint/Google Slides, live demo environment",
                EstimatedWeeks = 1,
                Dependencies = "Phase 11",
                Risks = "Demo failures; difficult committee questions",
                SuccessCriteria = "Successfully defended project before committee"
            },
        ];
    }
}
