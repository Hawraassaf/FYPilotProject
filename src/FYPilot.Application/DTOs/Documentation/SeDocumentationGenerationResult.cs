namespace FYPilot.Application.DTOs.Documentation;

/// <summary>
/// Why a generation attempt did not end in a persisted, displayable document.
/// Distinguishes failure stages so logs/UI never have to guess from a free-text
/// message, and so "no fallback was saved" can always be paired with a
/// specific, honest reason.
/// </summary>
public enum SeDocumentationErrorCode
{
    None,
    NetworkFailure,
    Timeout,
    NonSuccessResponse,
    DeserializationFailure,
    InvalidResponse,
    ProviderUnavailable,
    ReviewRejected,
    // A real provider responded (llmUsed may even read true) but at least
    // one core section -- or an applicable AI technical report -- used
    // deterministic fallback content instead of real output. Kept distinct
    // from ProviderUnavailable/ReviewRejected so logs/UI can say precisely
    // why a document with a real provider response was still rejected.
    CoreSectionFallback,
    // Python's Writer stage ran out of its own reserved budget before every
    // core section could even be ATTEMPTED (see WriterBudgetExceededError
    // in se_documentation_orchestrator.py) -- distinct from
    // ProviderUnavailable (a provider call was attempted and failed) and
    // from CoreSectionFallback (every section was attempted, some just
    // fell back to deterministic content).
    WriterBudgetExceeded,
    PersistenceFailure,
    UnexpectedError,
}

/// <summary>
/// The single result contract for one SE Documentation generation attempt.
/// Replaces returning a bare GeneratedDocumentationDto (which could not
/// distinguish "real AI success" from "AI success with review warnings" from
/// "nothing usable came back") and replaces catch-and-return-null (which lost
/// the reason entirely). See DocumentationGeneratorService.GenerateAsync.
///
/// Succeeded/Persisted/Documentation are only ever set together: a
/// generation attempt either produces a validated, persisted, displayable
/// document, or it produces none of those three -- there is no partial state
/// where a document exists but wasn't validated, or was validated but not
/// persisted.
/// </summary>
public sealed record SeDocumentationGenerationResult(
    bool Succeeded,
    bool Persisted,
    bool UsedLlm,
    bool Displayable,
    string? Source,
    string? Provider,
    string? Model,
    string? ReviewStatus,
    string? OutputOrigin,
    string? OutputReviewLevel,
    string? Warning,
    SeDocumentationErrorCode ErrorCode,
    string? UserMessage,
    GeneratedDocumentationDto? Documentation)
{
    public static SeDocumentationGenerationResult Success(
        GeneratedDocumentationDto documentation,
        bool usedLlm,
        string? source,
        string? provider,
        string? model,
        string? reviewStatus,
        string? outputOrigin,
        string? outputReviewLevel,
        string? warning) =>
        new(
            Succeeded: true,
            Persisted: true,
            UsedLlm: usedLlm,
            Displayable: true,
            Source: source,
            Provider: provider,
            Model: model,
            ReviewStatus: reviewStatus,
            OutputOrigin: outputOrigin,
            OutputReviewLevel: outputReviewLevel,
            Warning: warning,
            ErrorCode: SeDocumentationErrorCode.None,
            UserMessage: null,
            Documentation: documentation);

    public static SeDocumentationGenerationResult Failure(
        SeDocumentationErrorCode errorCode,
        string userMessage,
        string? reviewStatus = null,
        string? warning = null) =>
        new(
            Succeeded: false,
            Persisted: false,
            UsedLlm: false,
            Displayable: false,
            Source: null,
            Provider: null,
            Model: null,
            ReviewStatus: reviewStatus,
            OutputOrigin: null,
            OutputReviewLevel: null,
            Warning: warning,
            ErrorCode: errorCode,
            UserMessage: userMessage,
            Documentation: null);
}
