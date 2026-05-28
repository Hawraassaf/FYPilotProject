using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/feedback")]
[Authorize]
public class FeedbackController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);

    private static FeedbackResponse Map(Feedback f) => new(
        f.Id, f.Content, f.Rating, f.ProjectId, f.SupervisorId,
        f.Supervisor?.FullName ?? "Supervisor",
        f.CreatedAt.ToString("o")
    );

    [HttpGet("project/{projectId}")]
    public async Task<IActionResult> ListByProject(int projectId)
    {
        var feedbacks = await db.Feedbacks
            .Include(f => f.Supervisor)
            .Where(f => f.ProjectId == projectId)
            .OrderByDescending(f => f.CreatedAt)
            .ToListAsync();
        return Ok(feedbacks.Select(Map));
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CreateFeedbackRequest request)
    {
        var feedback = new Feedback
        {
            Content = request.Content,
            Rating = request.Rating,
            ProjectId = request.ProjectId,
            SupervisorId = UserId,
        };
        db.Feedbacks.Add(feedback);
        await db.SaveChangesAsync();
        await db.Entry(feedback).Reference(f => f.Supervisor).LoadAsync();
        return StatusCode(201, Map(feedback));
    }
}
