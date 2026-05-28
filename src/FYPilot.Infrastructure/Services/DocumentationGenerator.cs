using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Infrastructure.Services;

public static class DocumentationGenerator
{
    public static DocumentationResponse Generate(ProjectIdea idea)
    {
        return new DocumentationResponse(
            idea.Title,
            $"This project presents {idea.Title}, a software system designed to address {idea.ProblemStatement.ToLower()} " +
            $"The system targets {idea.TargetUsers} in the Lebanese market, particularly in the {idea.LebanesesSector} sector. " +
            $"Built using {idea.RequiredTechnologies}, the system achieves a feasibility score of {idea.FeasibilityScore}/100 " +
            $"and innovation score of {idea.InnovationScore}/100.",
            idea.ProblemStatement,
            $"1. Develop a fully functional {idea.Title} system\n" +
            $"2. Address the needs of {idea.TargetUsers}\n" +
            $"3. Integrate AI/data science components where applicable\n" +
            $"4. Deploy a production-ready system\n" +
            $"5. Provide comprehensive documentation and testing",
            $"The system covers: user authentication and authorization, core {idea.Domain} features, " +
            $"Lebanese market-specific functionality ({idea.LebanesesSector}), admin management, " +
            $"and {(idea.Domain.Contains("AI") ? "AI/ML model integration" : "data analytics")}. " +
            $"Out of scope: mobile application (unless specified), third-party payment integration.",
            $"The project follows an Agile-inspired development methodology with 12 structured phases. " +
            $"The backend is developed using C# ASP.NET Core with Entity Framework Core for data management. " +
            $"{(idea.Domain.Contains("AI") ? "A Python FastAPI microservice handles the AI/ML components." : "A Python service handles data analytics.")} " +
            $"The frontend uses React with TailwindCSS for a modern, responsive UI.",
            $"1. A deployed web application accessible to {idea.TargetUsers}\n" +
            $"2. REST API with full Swagger documentation\n" +
            $"3. {(idea.Domain.Contains("AI") ? "Trained ML model with >75% accuracy" : "Analytics dashboard")}\n" +
            $"4. Complete test suite with 80%+ coverage\n" +
            $"5. Full technical documentation package\n" +
            $"6. Demonstrated business value for the Lebanese {idea.LebanesesSector} sector",
            idea.RequiredTechnologies,
            $"Phase 1-2: Research & Requirements (2 weeks)\n" +
            $"Phase 3-4: Design & Database (2 weeks)\n" +
            $"Phase 5-6: Backend & Python Development (5 weeks)\n" +
            $"Phase 7: Frontend Development (3 weeks)\n" +
            $"Phase 8-9: Integration & Testing (2 weeks)\n" +
            $"Phase 10-12: Deployment, Documentation & Presentation (3 weeks)\n" +
            $"Total: {idea.ExpectedDurationWeeks} weeks",
            "[1] ASP.NET Core 8 Documentation - Microsoft\n" +
            "[2] Entity Framework Core - Microsoft\n" +
            "[3] React Documentation - Meta\n" +
            "[4] FastAPI Documentation - Sebastián Ramírez\n" +
            "[5] PostgreSQL Documentation\n" +
            "[6] [Add Lebanese market research references here]\n" +
            "[7] [Add domain-specific academic papers here]"
        );
    }
}
