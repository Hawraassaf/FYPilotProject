using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Infrastructure.Services;

public static class PresentationGenerator
{
    public static PresentationResponse Generate(ProjectIdea idea)
    {
        var slides = new List<SlideOutline>
        {
            new(1, $"{idea.Title} — FYP Presentation", ["Your Name, ID, Major", "Supervisor Name", "University, Academic Year"]),
            new(2, "Problem Statement", [idea.ProblemStatement, $"Target Audience: {idea.TargetUsers}", $"Lebanese Context: {idea.LebaneseMarketRelevance}"]),
            new(3, "Why This Project Matters", [idea.WhyUseful, $"Lebanese Market Opportunity: {idea.LebanesesSector} sector", "Gap in existing solutions"]),
            new(4, "Proposed Solution", [$"{idea.Title} — what it does", "Key features (list top 4-5)", "How it solves the problem"]),
            new(5, "System Architecture", ["3-tier architecture: Frontend → .NET API → PostgreSQL", $"Optional: Python microservice for {(idea.Domain.Contains("AI") ? "AI/ML" : "analytics")}", "Diagram of component interactions"]),
            new(6, "Technology Stack", [$"Backend: {idea.RequiredTechnologies.Split(',').FirstOrDefault()?.Trim() ?? "ASP.NET Core"}", "Frontend: React + TailwindCSS", "Database: PostgreSQL", $"AI/Analytics: Python FastAPI"]),
            new(7, "Database Design", ["ER Diagram walkthrough", "Key entities and relationships", "Sample data demonstration"]),
            new(8, "Live Demo", ["Authentication flow", "Core feature demonstration", "Admin/dashboard view", "Mobile responsiveness"]),
            new(9, "AI/Data Science Component", [idea.Domain.Contains("AI") ? "ML model architecture and training process" : "Analytics and reporting features", "Sample predictions/results", "Accuracy and performance metrics"]),
            new(10, "Testing & Quality", ["Unit tests: xUnit (.NET) + pytest (Python)", "Integration tests: API testing with Postman", "UI tests: Playwright end-to-end", "Coverage: 80%+"]),
            new(11, "Lebanese Market Impact", [$"Sector: {idea.LebanesesSector}", "Problem it solves locally", "Potential business clients", "Scalability for Lebanese market"]),
            new(12, "Conclusion & Future Work", ["Project achievements", "Limitations encountered", "Future enhancements (v2 features)", "Questions?"]),
        };

        var questions = new List<QnA>
        {
            new("Why did you choose this tech stack?", $"We chose C# ASP.NET Core for its enterprise-grade reliability, performance, and strong ecosystem. React provides a responsive UI. {(idea.Domain.Contains("AI") ? "Python was selected for its superior ML libraries (scikit-learn, pandas)." : "PostgreSQL provides ACID compliance and scalability.")} This combination gives us the best of each technology."),
            new("How does your system scale?", "The system uses a microservices-inspired architecture where the .NET API, Python service, and frontend are independently deployable. PostgreSQL handles concurrent connections efficiently. We can add caching (Redis) and horizontal scaling for production load."),
            new("What are the security measures?", "We implement JWT authentication with 7-day expiry, bcrypt password hashing (cost factor 12), input validation on all endpoints, protection against SQL injection via EF Core parameterized queries, and CORS restrictions."),
            new("How did you handle the Lebanese market context?", $"We researched the {idea.LebanesesSector} sector specifically. {idea.LebaneseMarketRelevance} We interviewed potential users and designed features around local needs and constraints."),
            new("What would you do differently?", "We would start with a more detailed requirements phase, use Docker from day one for consistent environments, and implement CI/CD earlier in the project. We also underestimated the complexity of the {idea.Domain} components."),
            new("What is the innovation aspect of this project?", $"The innovation score is {idea.InnovationScore}/100. The unique aspect is {idea.LebaneseMarketRelevance} Combined with our AI component, this creates a solution that doesn't exist in the Lebanese market."),
        };

        var demoFlow = new List<string>
        {
            "1. Show landing page and login screen",
            "2. Register as a new user (live)",
            "3. Complete profile setup",
            "4. Demonstrate core feature (main use case)",
            "5. Show admin/supervisor dashboard",
            "6. Run a sample prediction/analytics query",
            "7. Show API documentation via Swagger",
            "8. Brief code walkthrough of key component"
        };

        return new PresentationResponse(idea.Title, slides, demoFlow, questions);
    }
}
