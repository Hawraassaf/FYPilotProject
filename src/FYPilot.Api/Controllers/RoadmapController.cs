using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FYPilot.Api.Controllers;

/// <summary>
/// DISABLED (Roadmap fallback-provenance stabilization). This controller
/// used to call the static <see cref="FYPilot.Infrastructure.Services.RoadmapGenerator"/>
/// -- a fully hardcoded, non-AI, non-reviewed 12-phase template that
/// recommends forbidden technologies (React, React Native, AWS/Azure/
/// Kubernetes) -- and wrote to the SAME ProjectRoadmap/RoadmapPhase tables
/// the real Roadmap pipeline uses (Razor Page ->
/// IAiServiceClient.GenerateProjectRoadmapAsync -> Python
/// ProjectRoadmapAgent, see src/FYPilot.Web/Pages/Student/Roadmap.cshtml.cs).
/// Nothing in the product calls this controller (FYPilot.Api is a
/// separately-run process, not started by the AppHost by default), but a
/// stray call to it -- authorized or not -- could silently overwrite a
/// student's real, AI-reviewed roadmap with generic, unreviewed content.
///
/// Every route now returns 410 Gone rather than being deleted outright, so
/// a caller gets an explicit, actionable error instead of a 404 (which
/// looks like "this was never a route") or silent data corruption. The
/// route itself is kept discoverable (rather than removed from routing)
/// specifically so this message is what anyone hitting the old URL sees.
/// </summary>
[ApiController]
[Route("api/roadmap")]
[Authorize]
public class RoadmapController : ControllerBase
{
    private const string DisabledMessage =
        "This legacy Roadmap API is disabled. It generated generic, " +
        "non-AI, unreviewed roadmap content and has been superseded by " +
        "the AI-generated, reviewed Roadmap on the Student Roadmap page " +
        "(Razor Page -> AI service -> ProjectRoadmapAgent). It no longer " +
        "reads or writes roadmap data.";

    [HttpGet("{ideaId}")]
    public IActionResult Get(int ideaId) => Gone();

    [HttpPost("{ideaId}/generate")]
    public IActionResult Generate(int ideaId) => Gone();

    [HttpPatch("phases/{phaseId}/complete")]
    public IActionResult MarkComplete(int phaseId) => Gone();

    private ObjectResult Gone() => StatusCode(StatusCodes.Status410Gone, new { message = DisabledMessage });
}
