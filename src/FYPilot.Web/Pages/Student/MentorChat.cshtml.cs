using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class MentorChatModel(ApplicationDbContext db) : PageModel
{
    public List<ChatMessage>  Messages       { get; private set; } = [];
    public List<ProjectIdea>  Ideas          { get; private set; } = [];
    public int?               SelectedIdeaId { get; private set; }

    public async Task OnGetAsync(int? ideaId)
    {
        await LoadAsync(ideaId);
    }

    public async Task<IActionResult> OnPostAsync(string message, int? ideaId)
    {
        if (string.IsNullOrWhiteSpace(message)) return RedirectToPage(new { ideaId });

        var userId = UserId();
        var idea   = ideaId.HasValue ? await db.ProjectIdeas.FindAsync(ideaId) : null;
        var skills = await db.StudentSkills.Where(s => s.UserId == userId).ToListAsync();

        db.ChatMessages.Add(new ChatMessage { UserId = userId, IdeaId = ideaId, Role = "user",      Content = message });

        var reply = AiMentor.GetResponse(message, idea, skills, null);
        db.ChatMessages.Add(new ChatMessage { UserId = userId, IdeaId = ideaId, Role = "assistant", Content = reply });

        await db.SaveChangesAsync();
        await LoadAsync(ideaId);
        return Page();
    }

    private async Task LoadAsync(int? ideaId)
    {
        var userId     = UserId();
        SelectedIdeaId = ideaId;
        Ideas          = await db.ProjectIdeas.Where(i => i.UserId == userId).Take(5).ToListAsync();
        Messages       = await db.ChatMessages.Where(m => m.UserId == userId && m.IdeaId == ideaId)
                             .OrderBy(m => m.CreatedAt).Take(50).ToListAsync();
    }

    private int UserId() => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}
