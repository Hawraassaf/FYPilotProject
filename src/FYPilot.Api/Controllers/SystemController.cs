using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Mvc;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/system")]
public class SystemController : ControllerBase
{
    private readonly IAiServiceClient _ai;
    private readonly ApplicationDbContext _db;

    public SystemController(IAiServiceClient ai, ApplicationDbContext db)
    {
        _ai = ai;
        _db = db;
    }

    /// <summary>GET /api/system/health — .NET API health (no DB required)</summary>
    [HttpGet("health")]
    public IActionResult Health() => Ok(new
    {
        status  = "ok",
        service = "FYPilot .NET API",
        version = "2.0.0",
        architecture = "Clean Architecture",
        time    = DateTime.UtcNow
    });

    /// <summary>GET /api/system/ai-health — Calls Python /health through IAiServiceClient</summary>
    [HttpGet("ai-health")]
    public async Task<IActionResult> AiHealth()
    {
        var result = await _ai.GetHealthAsync();
        if (result == null)
            return StatusCode(502, new { status = "error", message = "Cannot reach Python AI service" });
        return Ok(result);
    }

    /// <summary>GET /api/system/python-health — Alias for ai-health (backwards compat)</summary>
    [HttpGet("python-health")]
    public Task<IActionResult> PythonHealth() => AiHealth();

    /// <summary>POST /api/system/test-skill-analysis — End-to-end skill analysis test</summary>
    [HttpPost("test-skill-analysis")]
    public async Task<IActionResult> TestSkillAnalysis([FromBody] SkillAnalysisRequest request)
    {
        var result = await _ai.AnalyzeSkillsAsync(request);
        if (result == null)
            return StatusCode(502, new { status = "error", message = "AI skill analysis failed" });
        return Ok(result);
    }

    /// <summary>GET /api/system/database-health — Checks DB connectivity</summary>
    [HttpGet("database-health")]
    public async Task<IActionResult> DatabaseHealth()
    {
        try
        {
            var canConnect = await _db.Database.CanConnectAsync();
            if (!canConnect)
                return StatusCode(503, new { status = "error", message = "Cannot connect to database" });

            var userCount = _db.Users.Count();
            return Ok(new { status = "ok", message = "Database connected", userCount });
        }
        catch (Exception ex)
        {
            return StatusCode(503, new { status = "error", message = ex.Message });
        }
    }
}
