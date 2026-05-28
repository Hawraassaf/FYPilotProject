namespace FYPilot.Domain.Entities;

public class ProjectDocumentation
{
    public int Id { get; set; }

    public int UserId { get; set; }
    public int ProjectIdeaId { get; set; }

    public string Title { get; set; } = string.Empty;

    public string FunctionalRequirementsJson { get; set; } = "[]";
    public string NonFunctionalRequirementsJson { get; set; } = "[]";
    public string UseCasesJson { get; set; } = "[]";
    public string EdgeCasesJson { get; set; } = "[]";
    public string DatabaseDesignJson { get; set; } = "[]";
    public string UiDesignJson { get; set; } = "[]";
    public string DiagramDescriptionsJson { get; set; } = "{}";
    public string AiTechnicalReportJson { get; set; } = "{}";

    public string SupervisorStatus { get; set; } = "Draft";
    public string SupervisorComment { get; set; } = string.Empty;

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;
}