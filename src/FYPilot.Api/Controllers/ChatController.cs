using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Services;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/chat")]
[Authorize]
public class ChatController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);

    [HttpGet]
    public async Task<IActionResult> GetHistory([FromQuery] int? ideaId)
    {
        var query = db.ChatMessages.Where(m => m.UserId == UserId);
        if (ideaId.HasValue) query = query.Where(m => m.IdeaId == ideaId);
        var msgs = await query.OrderBy(m => m.CreatedAt).ToListAsync();
        return Ok(new ChatHistoryResponse(msgs.Select(m => new ChatMessageDto(m.Id, m.Role, m.Content, m.CreatedAt)).ToList()));
    }

    [HttpPost]
    public async Task<IActionResult> Send([FromBody] ChatRequest request)
    {
        ProjectIdea? idea = null;
        if (request.IdeaId.HasValue)
            idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == request.IdeaId && i.UserId == UserId);

        var skills = await db.StudentSkills.Where(s => s.UserId == UserId).ToListAsync();
        var profile = await db.StudentProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);

        db.ChatMessages.Add(new ChatMessage { UserId = UserId, IdeaId = request.IdeaId, Role = "user", Content = request.Message });

        var reply = AiMentor.GetResponse(request.Message, idea, skills, profile);
        var botMsg = new ChatMessage { UserId = UserId, IdeaId = request.IdeaId, Role = "assistant", Content = reply };
        db.ChatMessages.Add(botMsg);
        await db.SaveChangesAsync();

        return Ok(new ChatResponse(reply, "assistant", botMsg.CreatedAt));
    }

    [HttpDelete]
    public async Task<IActionResult> ClearHistory([FromQuery] int? ideaId)
    {
        var query = db.ChatMessages.Where(m => m.UserId == UserId);
        if (ideaId.HasValue) query = query.Where(m => m.IdeaId == ideaId);
        var msgs = await query.ToListAsync();
        db.ChatMessages.RemoveRange(msgs);
        await db.SaveChangesAsync();
        return NoContent();
    }
}
