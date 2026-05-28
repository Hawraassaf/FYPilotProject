namespace FYPilot.Application.DTOs.Documentation;

public class GeneratedDocumentationDto
{
    public int Id { get; set; }

    public string Title { get; set; } = string.Empty;

    public List<string> FunctionalRequirements { get; set; } = new();
    public List<string> NonFunctionalRequirements { get; set; } = new();
    public List<string> UseCases { get; set; } = new();
    public List<string> EdgeCases { get; set; } = new();
    public List<string> DatabaseDesign { get; set; } = new();
    public List<string> UiDesign { get; set; } = new();

    public string DiagramDescriptions { get; set; } = string.Empty;
    public string AiTechnicalReport { get; set; } = string.Empty;

    public string SupervisorStatus { get; set; } = "Draft";
    public string SupervisorComment { get; set; } = string.Empty;
}