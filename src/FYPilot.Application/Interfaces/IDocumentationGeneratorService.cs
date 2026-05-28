using FYPilot.Application.DTOs.Documentation;

namespace FYPilot.Application.Interfaces;

public interface IDocumentationGeneratorService
{
    Task<GeneratedDocumentationDto> GenerateAsync(GenerateDocumentationRequest request);
    Task<GeneratedDocumentationDto?> GetByIdAsync(int id);
    Task<List<GeneratedDocumentationDto>> GetByUserIdAsync(int userId);
    Task<List<GeneratedDocumentationDto>> GetAllForSupervisorAsync();
    Task AddSupervisorFeedbackAsync(int documentationId, string status, string comment);
}