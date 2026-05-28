namespace FYPilot.Application.DTOs.Documentation;

public class GenerateDocumentationRequest
{
    public int UserId { get; set; }
    public int ProjectIdeaId { get; set; }

    public string ProjectTitle { get; set; } = string.Empty;
    public string ProjectDescription { get; set; } = string.Empty;
    public string Domain { get; set; } = string.Empty;
    public string TechStack { get; set; } = string.Empty;

    public bool ContainsAi { get; set; }
}