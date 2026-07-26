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
public class SkillAssessmentModel(
    ApplicationDbContext db,
    IAiServiceClient ai,
    ILogger<SkillAssessmentModel> logger)
    : PageModel
{
    public List<StudentSkill> ExistingSkills { get; private set; } = [];

    public SkillAnalysisResponse? Result { get; private set; }

    public string? SuccessMessage { get; private set; }

    public string? ErrorMessage { get; private set; }

    public Dictionary<string, List<string>>
        SkillCategories
    { get; } = new()
    {
        ["Programming Languages"] =
        [
            "Python",
            "C#",
            "Java",
            "JavaScript",
            "TypeScript",
            "C++",
            "PHP",
            "Go",
            "Kotlin"
        ],

        ["Web & Frontend"] =
        [
            "React",
            "Angular",
            "Vue.js",
            "HTML/CSS",
            "Blazor",
            "ASP.NET Core",
            "Bootstrap"
        ],

        ["Backend & APIs"] =
        [
            "Node.js",
            "Express",
            "FastAPI",
            "Django",
            "Spring Boot",
            "REST API",
            "GraphQL"
        ],

        ["Databases"] =
        [
            "PostgreSQL",
            "MySQL",
            "MongoDB",
            "Redis",
            "SQL Server",
            "SQLite",
            "Firebase"
        ],

        ["AI & Data Science"] =
        [
            "Machine Learning",
            "Deep Learning",
            "NLP",
            "Computer Vision",
            "scikit-learn",
            "TensorFlow",
            "PyTorch"
        ],

        ["DevOps & Cloud"] =
        [
            "Docker",
            "Kubernetes",
            "AWS",
            "Azure",
            "CI/CD",
            "Linux",
            "Git"
        ],

        ["Mobile"] =
        [
            "React Native",
            "Flutter",
            "Android",
            "iOS",
            "Expo"
        ]
    };

    public async Task OnGetAsync(
        CancellationToken cancellationToken)
    {
        await LoadExistingSkillsAsync(
            UserId(),
            cancellationToken);
    }

    public async Task<IActionResult> OnPostAsync(
        [FromForm] List<string>? SelectedSkills,
        [FromForm] Dictionary<string, int>? Ratings,
        CancellationToken cancellationToken)
    {
        var userId = UserId();

        SelectedSkills ??= [];
        Ratings ??= [];

        var cleanedSelectedSkills =
            SelectedSkills
                .Where(skill =>
                    !string.IsNullOrWhiteSpace(skill))
                .Select(skill =>
                    skill.Trim())
                .Distinct(
                    StringComparer.OrdinalIgnoreCase)
                .ToList();

        if (cleanedSelectedSkills.Count == 0)
        {
            ErrorMessage =
                "Please select at least one skill "
                + "before saving.";

            await LoadExistingSkillsAsync(
                userId,
                cancellationToken);

            return Page();
        }

        /*
         * Save the student skills first.
         *
         * This database operation must not depend on
         * whether the external Python AI service is
         * currently available.
         */
        var oldSkills =
            await db.StudentSkills
                .Where(skill =>
                    skill.UserId == userId)
                .ToListAsync(
                    cancellationToken);

        db.StudentSkills.RemoveRange(
            oldSkills);

        foreach (var skill
                 in cleanedSelectedSkills)
        {
            var rating =
                Ratings.TryGetValue(
                    skill,
                    out var submittedRating)
                    ? submittedRating
                    : 3;

            rating =
                Math.Clamp(
                    rating,
                    1,
                    5);

            db.StudentSkills.Add(
                new StudentSkill
                {
                    UserId =
                        userId,

                    SkillName =
                        skill,

                    Rating =
                        rating
                });
        }

        await db.SaveChangesAsync(
            cancellationToken);

        /*
         * AI analysis is optional.
         *
         * When the Python service is unavailable, the
         * saved skills remain in PostgreSQL and the
         * student receives a normal message instead of
         * an Internal Server Error.
         */
        try
        {
            Result =
                await ai.AnalyzeSkillsAsync(
                    new SkillAnalysisRequest(
                        cleanedSelectedSkills,
                        "intermediate"));

            SuccessMessage =
                "Skill profile saved and analyzed "
                + "successfully.";
        }
        catch (HttpRequestException exception)
        {
            logger.LogWarning(
                exception,
                "Skill profile was saved for user "
                + "{UserId}, but the AI service could "
                + "not be reached.",
                userId);

            Result =
                null;

            SuccessMessage =
                "Skill profile saved successfully. "
                + "AI analysis is temporarily unavailable.";
        }
        catch (TaskCanceledException exception)
        {
            logger.LogWarning(
                exception,
                "Skill profile was saved for user "
                + "{UserId}, but the AI analysis request "
                + "timed out.",
                userId);

            Result =
                null;

            SuccessMessage =
                "Skill profile saved successfully. "
                + "AI analysis took too long and will "
                + "be available when the AI service "
                + "is running.";
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Skill profile was saved for user "
                + "{UserId}, but AI analysis failed.",
                userId);

            Result =
                null;

            SuccessMessage =
                "Skill profile saved successfully. "
                + "AI analysis could not be generated "
                + "right now.";
        }

        await LoadExistingSkillsAsync(
            userId,
            cancellationToken);

        return Page();
    }

    private async Task LoadExistingSkillsAsync(
        int userId,
        CancellationToken cancellationToken)
    {
        ExistingSkills =
            await db.StudentSkills
                .AsNoTracking()
                .Where(skill =>
                    skill.UserId == userId)
                .OrderByDescending(skill =>
                    skill.Rating)
                .ThenBy(skill =>
                    skill.SkillName)
                .ToListAsync(
                    cancellationToken);
    }

    private int UserId()
    {
        var value =
            User.FindFirst(
                ClaimTypes.NameIdentifier)
                ?.Value;

        if (int.TryParse(
                value,
                out var userId))
        {
            return userId;
        }

        throw new InvalidOperationException(
            "Unable to identify the logged-in student.");
    }
}