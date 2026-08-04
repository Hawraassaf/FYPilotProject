using FYPilot.Application.DTOs.Documentation;

namespace FYPilot.Application.Interfaces;

public interface IDocumentationGeneratorService
{
    /// <summary>
    /// Generates SE documentation for the given project idea. Never persists
    /// or returns hardcoded/template fallback content -- on any failure
    /// (network, provider, deserialization, or a non-usable/non-displayable
    /// review result) this returns a typed failure result and leaves any
    /// previously persisted valid document for the idea untouched.
    /// </summary>
    Task<SeDocumentationGenerationResult> GenerateAsync(GenerateDocumentationRequest request);
    Task<GeneratedDocumentationDto?> GetByIdAsync(int id);
    Task<List<GeneratedDocumentationDto>> GetByUserIdAsync(int userId);
    Task<List<GeneratedDocumentationDto>> GetAllForSupervisorAsync();
    Task AddSupervisorFeedbackAsync(int documentationId, string status, string comment);
    Task<GeneratedDocumentationDto?> GetLatestForIdeaAsync(
     int projectIdeaId,
     CancellationToken cancellationToken = default);
}