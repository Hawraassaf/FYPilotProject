using FYPilot.Domain.Entities;

namespace FYPilot.Infrastructure.Services;

public static class AiMentor
{
    private static readonly Random Rng = new();

    public static string GetResponse(string message, ProjectIdea? idea, List<StudentSkill> skills, StudentProfile? profile)
    {
        var msg = message.ToLower();
        var projectTitle = idea?.Title ?? "your project";
        var userName = profile?.User?.FullName ?? "student";

        // Phase explanations
        if (msg.Contains("phase") || msg.Contains("roadmap"))
            return $"Your {projectTitle} roadmap has 12 phases: Research, Requirements, System Design, Database Design, .NET Backend, Python/Data Science, Frontend, Integration, Testing, Deployment, Documentation, and Final Presentation. Each phase builds on the previous one. Which phase would you like to explore in detail?";

        // C# / .NET
        if (msg.Contains("c#") || msg.Contains("dotnet") || msg.Contains(".net") || msg.Contains("asp"))
            return GetDotNetAdvice(idea);

        // Python / AI / ML
        if (msg.Contains("python") || msg.Contains("machine learning") || msg.Contains("ai") || msg.Contains("ml") || msg.Contains("data science"))
            return GetPythonAdvice(idea);

        // Database
        if (msg.Contains("database") || msg.Contains("sql") || msg.Contains("entity framework") || msg.Contains("schema"))
            return $"For {projectTitle}, use PostgreSQL as your database. With Entity Framework Core, define your entities as C# classes and use migrations to create the schema. Start with your core entities, then add relationships. Use `dotnet ef migrations add InitialCreate` then `dotnet ef database update`.";

        // Architecture
        if (msg.Contains("architecture") || msg.Contains("design"))
            return $"For {projectTitle}, I recommend a 3-layer architecture: Presentation (React frontend) → API Layer (.NET Web API) → Data Layer (PostgreSQL). Optionally add a Python microservice for AI features. Use the Repository pattern in .NET to keep your data access logic separate from business logic.";

        // Testing
        if (msg.Contains("test") || msg.Contains("bug") || msg.Contains("error"))
            return $"For testing {projectTitle}: Use xUnit for .NET unit tests, pytest for Python, and Playwright for end-to-end tests. Write tests for your business logic first, then integration tests for API endpoints. Aim for 80%+ code coverage on critical services.";

        // Documentation
        if (msg.Contains("document") || msg.Contains("report") || msg.Contains("write"))
            return $"For {projectTitle} documentation, you need: 1) Software Requirements Specification (SRS) 2) Software Design Specification (SDS) 3) API Documentation (auto-generated via Swagger) 4) User Manual 5) Test Report. Use LaTeX or Word for the formal report. Your supervisor can generate these from the Documentation Generator.";

        // Presentation
        if (msg.Contains("present") || msg.Contains("defense") || msg.Contains("committee"))
            return $"For your {projectTitle} presentation: Structure it as Introduction (2 min) → Problem & Motivation (3 min) → System Demo (10 min) → Technical Architecture (3 min) → Testing Results (2 min) → Conclusion (2 min). Prepare for questions about scalability, security, and why you chose your tech stack.";

        // Skills
        if (msg.Contains("skill") || msg.Contains("learn") || msg.Contains("missing"))
        {
            var missingSkills = idea?.MissingSkills ?? "a few technical skills";
            return $"Based on your profile, you're missing: {missingSkills}. I recommend: Coursera/Udemy for {missingSkills.Split(',').FirstOrDefault()?.Trim() ?? "programming"}. Dedicate 2-3 weeks to upskilling before starting the project. Practice by building small exercises.";
        }

        // Next steps
        if (msg.Contains("next") || msg.Contains("start") || msg.Contains("begin"))
            return $"Great question! To start {projectTitle}: 1) Complete your skill assessment 2) Review the generated roadmap 3) Set up your development environment 4) Create your GitHub repository 5) Begin with the research phase. Don't rush — a solid foundation saves weeks later!";

        // Hello / greeting
        if (msg.Contains("hello") || msg.Contains("hi") || msg.Contains("hey"))
            return $"Hello! I'm your AI FYP mentor. I can help you with {projectTitle} — explaining roadmap phases, suggesting next steps, generating code snippets, helping with documentation, or answering technical questions. What would you like to know?";

        // Help / general
        if (msg.Contains("help"))
            return $"I can help you with: 1) Roadmap phases for {projectTitle} 2) C# .NET development guidance 3) Python/AI implementation advice 4) Database design 5) Testing strategies 6) Documentation writing 7) Presentation preparation. Just ask about any topic!";

        // Default intelligent response
        var responses = new[]
        {
            $"For {projectTitle}, that's an important consideration. Based on your profile and the project requirements, I suggest breaking this down into smaller tasks. Start with the foundational components and build incrementally. Would you like specific guidance on a particular aspect?",
            $"Good question about {projectTitle}! In software engineering, this type of problem is typically solved by applying separation of concerns. For your specific case with {idea?.RequiredTechnologies ?? "your tech stack"}, I'd recommend focusing on the core functionality first before adding advanced features.",
            $"When working on {projectTitle}, remember the MVP principle — build a Minimum Viable Product first. Your Lebanese market needs a solution that works reliably before it's feature-rich. What specific challenge are you facing right now?",
            $"For {projectTitle}, industry best practices suggest following SOLID principles in your .NET backend and clean architecture patterns. This will make your code maintainable and impress your evaluation committee. What part would you like me to elaborate on?"
        };
        return responses[Rng.Next(responses.Length)];
    }

    private static string GetDotNetAdvice(ProjectIdea? idea)
    {
        var title = idea?.Title ?? "your project";
        return $"For the C# .NET backend of {title}: Use ASP.NET Core 8 Web API with Clean Architecture (Controllers → Services → Repositories → DbContext). Key packages: Entity Framework Core (database), BCrypt.Net (passwords), Microsoft.AspNetCore.Authentication.JwtBearer (JWT). Structure: Controllers receive HTTP requests → Services contain business logic → Repositories handle data access. Always use dependency injection — register services in Program.cs with `builder.Services.AddScoped<IMyService, MyService>()`.";
    }

    private static string GetPythonAdvice(ProjectIdea? idea)
    {
        var title = idea?.Title ?? "your project";
        var isAi = idea?.Domain?.Contains("AI") == true;
        if (isAi)
            return $"For the Python AI/ML component of {title}: Use FastAPI for the API layer (fast, async, OpenAPI auto-docs). For ML: pandas for data processing, scikit-learn for classical ML, TensorFlow/PyTorch for deep learning. Pipeline: Load data → Clean/preprocess → Feature engineering → Train/evaluate model → Save model (joblib/pickle) → Serve predictions via FastAPI. Your .NET backend calls the Python service via HTTP. Use python-dotenv for environment variables.";

        return $"For the Python component of {title}: Use FastAPI to build a lightweight microservice. Focus on data processing, analytics, and utility functions. Your .NET backend will call this service via HTTP. Key libraries: pandas (data), requests (HTTP calls), matplotlib/seaborn (visualizations), FastAPI (API framework). Keep the service stateless for easy scaling.";
    }
}
