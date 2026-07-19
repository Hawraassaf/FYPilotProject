using System.Text.Json;
using FYPilot.Application.DTOs.Documentation;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;

namespace FYPilot.Infrastructure.Services;

public class DocumentationGeneratorService : IDocumentationGeneratorService
{
    private readonly ApplicationDbContext _db;
    private readonly IAiServiceClient _aiService;
    private readonly ILogger<DocumentationGeneratorService> _logger;

    public DocumentationGeneratorService(
        ApplicationDbContext db,
        IAiServiceClient aiService,
        ILogger<DocumentationGeneratorService> logger)
    {
        _db = db;
        _aiService = aiService;
        _logger = logger;
    }

    public async Task<GeneratedDocumentationDto> GenerateAsync(GenerateDocumentationRequest request)
    {
        var aiDocumentation = await TryGenerateWithAiAsync(request);

        var documentation = aiDocumentation != null
            ? BuildEntityFromAiDocumentation(request, aiDocumentation)
            : BuildEntityFromFallback(request);

        _db.ProjectDocumentations.Add(documentation);
        await _db.SaveChangesAsync();

        return ToDto(documentation);
    }

    private async Task<AiSeDocumentationDto?> TryGenerateWithAiAsync(GenerateDocumentationRequest request)
    {
        try
        {
            var aiRequest = await BuildAiRequestAsync(request);
            var response = await _aiService.GenerateSeDocumentationAsync(aiRequest);

            return response?.Documentation;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(
                ex,
                "AI SE documentation generation failed for project idea {ProjectIdeaId}. Falling back to deterministic generation.",
                request.ProjectIdeaId);

            return null;
        }
    }

    private async Task<AiSeDocumentationRequest> BuildAiRequestAsync(GenerateDocumentationRequest request)
    {
        var idea = await _db.ProjectIdeas
            .AsNoTracking()
            .FirstOrDefaultAsync(i => i.Id == request.ProjectIdeaId);

        var profile = await _db.StudentProfiles
            .AsNoTracking()
            .FirstOrDefaultAsync(p => p.UserId == request.UserId);

        var skills = await _db.StudentSkills
            .AsNoTracking()
            .Where(s => s.UserId == request.UserId)
            .ToListAsync();

        var roadmap = await _db.ProjectRoadmaps
            .Include(r => r.Phases)
            .AsNoTracking()
            .FirstOrDefaultAsync(r => r.IdeaId == request.ProjectIdeaId && r.UserId == request.UserId);

        var skillNames = skills
            .Select(s => s.SkillName)
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var skillRatings = skills
            .Where(s => !string.IsNullOrWhiteSpace(s.SkillName))
            .GroupBy(s => s.SkillName.Trim(), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(
                g => g.Key,
                g => Math.Clamp(g.First().Rating, 1, 5),
                StringComparer.OrdinalIgnoreCase);

        var studentProfile = new AiSeDocStudentProfile(
            Major: profile?.Major ?? "Computer Science",
            ExperienceLevel: profile?.ExperienceLevel ?? "intermediate",
            TeamSize: profile?.TeamMembers ?? 1,
            AvailableHoursPerWeek: profile?.AvailableHoursPerWeek ?? 10,
            Skills: skillNames,
            SkillRatings: skillRatings);

        var selectedIdea = new AiSeDocSelectedIdea(
            Id: idea?.Id,
            Title: idea?.Title ?? request.ProjectTitle,
            ProblemStatement: idea?.ProblemStatement ?? request.ProjectDescription,
            TargetUsers: idea?.TargetUsers ?? string.Empty,
            WhyUseful: idea?.WhyUseful ?? string.Empty,
            RequiredTechnologies: idea?.RequiredTechnologies ?? request.TechStack,
            RequiredSkills: idea?.RequiredSkills ?? string.Empty,
            MissingSkills: idea?.MissingSkills ?? string.Empty,
            DifficultyLevel: idea?.DifficultyLevel ?? "medium",
            ExpectedDurationWeeks: idea != null && idea.ExpectedDurationWeeks > 0 ? idea.ExpectedDurationWeeks : 10,
            Domain: idea?.Domain ?? request.Domain,
            FinalDeliverables: idea?.FinalDeliverables ?? string.Empty);

        var roadmapPhases = roadmap?.Phases
            .OrderBy(p => p.PhaseNumber)
            .Select(p => new AiSeDocRoadmapPhase(
                PhaseNumber: p.PhaseNumber,
                Name: p.Name,
                Objective: p.Objective,
                Tasks: DeserializeTasks(p.TasksJson),
                ExpectedOutput: p.ExpectedOutput,
                SuccessCriteria: p.SuccessCriteria,
                IsCompleted: p.IsCompleted))
            .ToList() ?? [];

        return new AiSeDocumentationRequest(
            StudentProfile: studentProfile,
            SelectedIdea: selectedIdea,
            Roadmap: roadmapPhases,
            ExistingNotes: string.Empty);
    }

    private static List<string> DeserializeTasks(string tasksJson)
    {
        try
        {
            return JsonSerializer.Deserialize<List<string>>(tasksJson) ?? [];
        }
        catch
        {
            return [];
        }
    }

    private static ProjectDocumentation BuildEntityFromAiDocumentation(
        GenerateDocumentationRequest request,
        AiSeDocumentationDto doc)
    {
        var functionalRequirements = doc.FunctionalRequirements
            .Select(r => $"{r.Id}: {r.Title} — {r.Description} (Priority: {r.Priority})")
            .ToList();

        var nonFunctionalRequirements = doc.NonFunctionalRequirements
            .Select(r => $"{r.Id}: {r.Title} — {r.Description} (Priority: {r.Priority})")
            .ToList();

        var useCases = doc.UseCases
            .Select(u => $"{u.Id}: {u.Title} — Actor: {u.Actor}. Goal: {u.Goal}.")
            .ToList();

        var edgeCases = doc.EdgeCases
            .Select(e => $"{e.Id}: {e.Scenario} — {e.ExpectedHandling}")
            .ToList();

        var databaseDesign = doc.DatabaseEntities
            .Select(e => $"{e.Name}: {e.Purpose} (Fields: {string.Join(", ", e.ImportantFields)})")
            .Concat(doc.EntityRelationships.Select(r =>
                $"{r.FromEntity} → {r.ToEntity} ({r.Type}): {r.Description}"))
            .ToList();

        var uiDesign = doc.SystemModules
            .Select(m => $"{m.Name} screen: {m.Responsibility}")
            .ToList();

        var diagramDescriptions =
            $"Entity Relationship Diagram (Mermaid):\n{doc.MermaidERD}\n\n" +
            $"Class Diagram (Mermaid):\n{doc.MermaidClassDiagram}\n\n" +
            $"Activity Diagram (Mermaid):\n{doc.ActivityDiagram}\n\n" +
            $"Sequence Diagram (Mermaid):\n{doc.SequenceDiagram}";

        var aiTechnicalReport =
            $"Architecture: {doc.Architecture.Style}\n\n" +
            $"{doc.Architecture.Explanation}\n\n" +
            $"AI / Service Layer: {doc.Architecture.AiService}\n\n" +
            "Risks and Limitations:\n" +
            string.Join("\n", doc.RisksAndLimitations.Select(r => $"- {r}")) + "\n\n" +
            "Expected Outcomes:\n" +
            string.Join("\n", doc.ExpectedOutcomes.Select(o => $"- {o}")) + "\n\n" +
            $"Documentation Quality Score: {doc.DocumentationQualityScore}/100";

        return new ProjectDocumentation
        {
            UserId = request.UserId,
            ProjectIdeaId = request.ProjectIdeaId,
            Title = $"{request.ProjectTitle} - Software Engineering Specification",

            FunctionalRequirementsJson = JsonSerializer.Serialize(functionalRequirements),
            NonFunctionalRequirementsJson = JsonSerializer.Serialize(nonFunctionalRequirements),
            UseCasesJson = JsonSerializer.Serialize(useCases),
            EdgeCasesJson = JsonSerializer.Serialize(edgeCases),
            DatabaseDesignJson = JsonSerializer.Serialize(databaseDesign),
            UiDesignJson = JsonSerializer.Serialize(uiDesign),
            DiagramDescriptionsJson = JsonSerializer.Serialize(diagramDescriptions),
            AiTechnicalReportJson = JsonSerializer.Serialize(aiTechnicalReport),

            SupervisorStatus = "Draft",
            SupervisorComment = string.Empty,
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow
        };
    }

    private static ProjectDocumentation BuildEntityFromFallback(GenerateDocumentationRequest request)
    {
        var functionalRequirements = GenerateFunctionalRequirements(request);
        var nonFunctionalRequirements = GenerateNonFunctionalRequirements(request);
        var useCases = GenerateUseCases(request);
        var edgeCases = GenerateEdgeCases(request);
        var databaseDesign = GenerateDatabaseDesign(request);
        var uiDesign = GenerateUiDesign(request);
        var diagramDescriptions = GenerateDiagramDescriptions(request);
        var aiTechnicalReport = GenerateAiTechnicalReport(request);

        return new ProjectDocumentation
        {
            UserId = request.UserId,
            ProjectIdeaId = request.ProjectIdeaId,
            Title = $"{request.ProjectTitle} - Software Engineering Specification",

            FunctionalRequirementsJson = JsonSerializer.Serialize(functionalRequirements),
            NonFunctionalRequirementsJson = JsonSerializer.Serialize(nonFunctionalRequirements),
            UseCasesJson = JsonSerializer.Serialize(useCases),
            EdgeCasesJson = JsonSerializer.Serialize(edgeCases),
            DatabaseDesignJson = JsonSerializer.Serialize(databaseDesign),
            UiDesignJson = JsonSerializer.Serialize(uiDesign),
            DiagramDescriptionsJson = JsonSerializer.Serialize(diagramDescriptions),
            AiTechnicalReportJson = JsonSerializer.Serialize(aiTechnicalReport),

            SupervisorStatus = "Draft",
            SupervisorComment = string.Empty,
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow
        };
    }

    public async Task<GeneratedDocumentationDto?> GetByIdAsync(int id)
    {
        var documentation = await _db.ProjectDocumentations
            .AsNoTracking()
            .FirstOrDefaultAsync(x => x.Id == id);

        return documentation == null ? null : ToDto(documentation);
    }

    public async Task<List<GeneratedDocumentationDto>> GetByUserIdAsync(int userId)
    {
        var documentations = await _db.ProjectDocumentations
            .AsNoTracking()
            .Where(x => x.UserId == userId)
            .OrderByDescending(x => x.CreatedAt)
            .ToListAsync();

        return documentations.Select(ToDto).ToList();
    }

    public async Task<List<GeneratedDocumentationDto>> GetAllForSupervisorAsync()
    {
        var documentations = await _db.ProjectDocumentations
            .AsNoTracking()
            .OrderByDescending(x => x.CreatedAt)
            .ToListAsync();

        return documentations.Select(ToDto).ToList();
    }

    public async Task AddSupervisorFeedbackAsync(int documentationId, string status, string comment)
    {
        var documentation = await _db.ProjectDocumentations
            .FirstOrDefaultAsync(x => x.Id == documentationId);

        if (documentation == null)
        {
            return;
        }

        documentation.SupervisorStatus = string.IsNullOrWhiteSpace(status) ? "Needs Revision" : status;
        documentation.SupervisorComment = comment ?? string.Empty;
        documentation.UpdatedAt = DateTime.UtcNow;

        await _db.SaveChangesAsync();
    }

    private static List<string> GenerateFunctionalRequirements(GenerateDocumentationRequest request)
    {
        return new List<string>
        {
            $"FR1: The system shall allow users to access the {request.ProjectTitle} platform securely.",
            $"FR2: The system shall allow users to create, view, update, and manage core {request.Domain} records.",
            "FR3: The system shall provide role-based access according to user type.",
            "FR4: The system shall validate user input before saving data.",
            "FR5: The system shall display dashboards and summaries for important project information.",
            "FR6: The system shall allow users to search, filter, and view relevant records.",
            "FR7: The system shall generate reports that summarize system activity and outcomes.",
            request.ContainsAi
                ? "FR8: The system shall send relevant data to an AI module and display explainable AI results."
                : "FR8: The system shall provide structured decision-support based on stored project data."
        };
    }

    private static List<string> GenerateNonFunctionalRequirements(GenerateDocumentationRequest request)
    {
        return new List<string>
        {
            "NFR1: The system should provide a clear and user-friendly interface.",
            "NFR2: The system should protect sensitive data using authentication and role-based authorization.",
            "NFR3: The system should store data reliably in a relational database.",
            "NFR4: The system should respond to common user actions within an acceptable time.",
            "NFR5: The system should be maintainable using layered architecture.",
            "NFR6: The system should allow future extension without major redesign.",
            "NFR7: The system should handle invalid input and service failures gracefully.",
            request.ContainsAi
                ? "NFR8: AI predictions should be explainable and presented as recommendations, not absolute decisions."
                : "NFR8: Reports should be clear, consistent, and easy to review by supervisors."
        };
    }

    private static List<string> GenerateUseCases(GenerateDocumentationRequest request)
    {
        return new List<string>
        {
            "UC1: User Login — The user logs in and is redirected based on their role.",
            $"UC2: Manage {request.Domain} Data — The user creates and manages records related to the project domain.",
            "UC3: View Dashboard — The user views key summaries, statistics, and alerts.",
            "UC4: Generate Report — The user generates structured project documentation.",
            "UC5: Supervisor Review — The supervisor reviews generated documentation and provides feedback.",
            request.ContainsAi
                ? "UC6: Run AI Analysis — The system analyzes project data and returns explainable AI results."
                : "UC6: Export/View Specification — The user reviews the generated software engineering specification."
        };
    }

    private static List<string> GenerateEdgeCases(GenerateDocumentationRequest request)
    {
        return new List<string>
        {
            "EC1: User submits an empty form — The system displays validation messages.",
            "EC2: Unauthorized user tries to access restricted page — The system blocks access.",
            "EC3: Database connection fails — The system shows a friendly error message.",
            "EC4: Required project information is missing — The system asks the user to complete missing fields.",
            "EC5: Supervisor rejects the documentation — The system marks it as Needs Revision.",
            request.ContainsAi
                ? "EC6: AI service is unavailable — The system falls back to normal report generation and warns the user."
                : "EC6: Report content is incomplete — The system allows regeneration or manual editing."
        };
    }

    private static List<string> GenerateDatabaseDesign(GenerateDocumentationRequest request)
    {
        return new List<string>
        {
            "User: stores login, role, and identity information.",
            "ProjectIdea: stores selected project title, description, domain, and owner.",
            "ProjectDocumentation: stores generated software engineering documentation sections.",
            "SupervisorFeedback: stores supervisor comments and review status.",
            "ReportSection: optional future table for storing each report section separately.",
            request.ContainsAi
                ? "AiAnalysisResult: stores AI scores, explanations, and risk outputs."
                : "SystemReport: stores generated summaries and review information."
        };
    }

    private static List<string> GenerateUiDesign(GenerateDocumentationRequest request)
    {
        return new List<string>
        {
            "Dashboard page with summary cards and navigation sidebar.",
            "Project details page showing title, description, domain, and technology stack.",
            "Documentation generator page with Generate button and section cards.",
            "Generated report page with tabs for requirements, use cases, database design, UI design, and AI report.",
            "Supervisor review page with status dropdown and comment box.",
            "System test page for verifying database and AI service connectivity."
        };
    }

    private static string GenerateDiagramDescriptions(GenerateDocumentationRequest request)
    {
        return $"""
        Use Case Diagram:
        Actors: Student/User, Supervisor, Admin, System, {(request.ContainsAi ? "AI Service" : "Report Generator")}.
        Main use cases: Login, Manage Project, Generate Documentation, Review Report, View Dashboard.

        Class Diagram:
        Main classes: User, ProjectIdea, ProjectDocumentation, SupervisorFeedback, ReportSection.

        Activity Diagram:
        User selects project idea → clicks Generate Documentation → system creates sections → user reviews → supervisor reviews → status updated.

        Data Flow Diagram:
        User input flows into the web application, then to application services, then to the database. {(request.ContainsAi ? "AI-related data may also be sent to the Python AI service for analysis." : "Reports are generated from project data and stored in PostgreSQL.")}
        """;
    }

    private static string GenerateAiTechnicalReport(GenerateDocumentationRequest request)
    {
        if (!request.ContainsAi)
        {
            return "This project does not require a dedicated AI technical report. The system can still include analytics and structured reporting.";
        }

        return $"""
        AI Technical Report for {request.ProjectTitle}

        Problem Type:
        The AI component is expected to support prediction, recommendation, classification, or intelligent decision support.

        Possible Input Features:
        - User profile data
        - Project/domain-specific records
        - Historical examples
        - Scores, categories, or behavioral indicators

        Possible Output:
        - Prediction score
        - Risk level
        - Recommendation
        - Classification result
        - Explanation or suggested action

        Suggested Models:
        - Logistic Regression for simple classification
        - Decision Tree for explainable rules
        - Random Forest for stronger prediction
        - TF-IDF and cosine similarity for text-based matching

        Evaluation Metrics:
        - Accuracy
        - Precision
        - Recall
        - F1-score
        - Confusion matrix

        Risks:
        - Dataset may be small or unavailable
        - Model predictions may be biased
        - Output should be treated as recommendation, not absolute truth

        Ethical Notes:
        - Avoid using sensitive personal data unless necessary
        - Explain AI outputs clearly
        - Allow human review before final decisions
        """;
    }

    private static GeneratedDocumentationDto ToDto(ProjectDocumentation documentation)
    {
        return new GeneratedDocumentationDto
        {
            Id = documentation.Id,
            Title = documentation.Title,
            FunctionalRequirements = DeserializeList(documentation.FunctionalRequirementsJson),
            NonFunctionalRequirements = DeserializeList(documentation.NonFunctionalRequirementsJson),
            UseCases = DeserializeList(documentation.UseCasesJson),
            EdgeCases = DeserializeList(documentation.EdgeCasesJson),
            DatabaseDesign = DeserializeList(documentation.DatabaseDesignJson),
            UiDesign = DeserializeList(documentation.UiDesignJson),
            DiagramDescriptions = DeserializeString(documentation.DiagramDescriptionsJson),
            AiTechnicalReport = DeserializeString(documentation.AiTechnicalReportJson),
            SupervisorStatus = documentation.SupervisorStatus,
            SupervisorComment = documentation.SupervisorComment
        };
    }

    private static List<string> DeserializeList(string json)
    {
        try
        {
            return JsonSerializer.Deserialize<List<string>>(json) ?? new List<string>();
        }
        catch
        {
            return new List<string>();
        }
    }

    private static string DeserializeString(string json)
    {
        try
        {
            return JsonSerializer.Deserialize<string>(json) ?? string.Empty;
        }
        catch
        {
            return json;
        }
    }
}