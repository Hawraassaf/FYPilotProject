using System.Security.Claims;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class SkillAssessmentModel(ApplicationDbContext db, IAiServiceClient ai) : PageModel
{
    public List<StudentSkill>       ExistingSkills    { get; private set; } = [];
    public SkillAnalysisResponse?   Result            { get; private set; }

    public Dictionary<string, List<string>> SkillCategories { get; } = new()
    {
        ["Programming Languages"] = ["Python","C#","Java","JavaScript","TypeScript","C++","PHP","Go","Kotlin"],
        ["Web & Frontend"]        = ["React","Angular","Vue.js","HTML/CSS","Blazor","ASP.NET Core","Bootstrap"],
        ["Backend & APIs"]        = ["Node.js","Express","FastAPI","Django","Spring Boot","REST API","GraphQL"],
        ["Databases"]             = ["PostgreSQL","MySQL","MongoDB","Redis","SQL Server","SQLite","Firebase"],
        ["AI & Data Science"]     = ["Machine Learning","Deep Learning","NLP","Computer Vision","scikit-learn","TensorFlow","PyTorch"],
        ["DevOps & Cloud"]        = ["Docker","Kubernetes","AWS","Azure","CI/CD","Linux","Git"],
        ["Mobile"]                = ["React Native","Flutter","Android","iOS","Expo"],
    };

    public async Task OnGetAsync()
    {
        ExistingSkills = await db.StudentSkills.Where(s => s.UserId == UserId()).ToListAsync();
    }

    public async Task<IActionResult> OnPostAsync(
        [FromForm] List<string> SelectedSkills,
        [FromForm] Dictionary<string, int> Ratings)
    {
        var userId = UserId();
        var old = db.StudentSkills.Where(s => s.UserId == userId);
        db.StudentSkills.RemoveRange(old);

        foreach (var skill in SelectedSkills)
        {
            var rating = Ratings.TryGetValue(skill, out var r) ? r : 3;
            db.StudentSkills.Add(new StudentSkill
            {
                UserId    = userId,
                SkillName = skill,
                Rating    = rating,
            });
        }
        await db.SaveChangesAsync();

        Result = await ai.AnalyzeSkillsAsync(new SkillAnalysisRequest(SelectedSkills, "intermediate"));
        ExistingSkills = await db.StudentSkills.Where(s => s.UserId == userId).ToListAsync();
        TempData["Success"] = "Skill profile saved.";
        return Page();
    }

    private int UserId() => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}
