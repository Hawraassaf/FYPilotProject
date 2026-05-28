using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class ProfileModel(ApplicationDbContext db) : PageModel
{
    [BindProperty]
    public ProfileInput Input { get; set; } = new();

    public string? SuccessMessage { get; set; }
    public string? ErrorMessage { get; set; }

    public class ProfileInput
    {
        [Required(ErrorMessage = "University is required.")]
        [StringLength(150)]
        public string University { get; set; } = "";

        [Required(ErrorMessage = "Major is required.")]
        [StringLength(100)]
        public string Major { get; set; } = "Computer Science";

        [Required(ErrorMessage = "Academic year is required.")]
        [StringLength(50)]
        public string Year { get; set; } = "3rd Year";

        [Required(ErrorMessage = "Experience level is required.")]
        public string ExperienceLevel { get; set; } = "beginner";

        [StringLength(500)]
        public string Interests { get; set; } = "";

        [Required(ErrorMessage = "Preferred domain is required.")]
        public string PreferredDomain { get; set; } = "";

        [StringLength(200)]
        public string PreferredStack { get; set; } = "";

        [Range(1, 60, ErrorMessage = "Available hours must be between 1 and 60.")]
        public int AvailableHoursPerWeek { get; set; } = 20;

        [Range(1, 6, ErrorMessage = "Team members must be between 1 and 6.")]
        public int TeamMembers { get; set; } = 1;

        [Required(ErrorMessage = "Target difficulty is required.")]
        public string TargetDifficulty { get; set; } = "intermediate";

        [StringLength(1000)]
        public string ProjectGoals { get; set; } = "";
    }

    public async Task<IActionResult> OnGetAsync()
    {
        var userId = GetCurrentUserId();

        var profile = await db.StudentProfiles
            .FirstOrDefaultAsync(p => p.UserId == userId);

        if (profile == null)
        {
            profile = new StudentProfile
            {
                UserId = userId,
                Major = "Computer Science",
                Year = "3rd Year",
                ExperienceLevel = "beginner",
                AvailableHoursPerWeek = 20,
                TeamMembers = 1,
                TargetDifficulty = "intermediate"
            };

            db.StudentProfiles.Add(profile);
            await db.SaveChangesAsync();
        }

        Input = new ProfileInput
        {
            University = profile.University,
            Major = profile.Major,
            Year = profile.Year,
            Interests = profile.Interests,
            ExperienceLevel = profile.ExperienceLevel,
            PreferredDomain = profile.PreferredDomain,
            PreferredStack = profile.PreferredStack,
            AvailableHoursPerWeek = profile.AvailableHoursPerWeek,
            TeamMembers = profile.TeamMembers,
            TargetDifficulty = profile.TargetDifficulty,
            ProjectGoals = profile.ProjectGoals
        };

        return Page();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        var userId = GetCurrentUserId();

        if (!ModelState.IsValid)
        {
            ErrorMessage = "Please correct the highlighted fields.";
            return Page();
        }

        var profile = await db.StudentProfiles
            .FirstOrDefaultAsync(p => p.UserId == userId);

        if (profile == null)
        {
            profile = new StudentProfile { UserId = userId };
            db.StudentProfiles.Add(profile);
        }

        profile.University = Input.University.Trim();
        profile.Major = Input.Major.Trim();
        profile.Year = Input.Year.Trim();
        profile.Interests = Input.Interests.Trim();
        profile.ExperienceLevel = Input.ExperienceLevel.Trim().ToLowerInvariant();
        profile.PreferredDomain = Input.PreferredDomain.Trim();
        profile.PreferredStack = Input.PreferredStack.Trim();
        profile.AvailableHoursPerWeek = Input.AvailableHoursPerWeek;
        profile.TeamMembers = Input.TeamMembers;
        profile.TargetDifficulty = Input.TargetDifficulty.Trim().ToLowerInvariant();
        profile.ProjectGoals = Input.ProjectGoals.Trim();

        await db.SaveChangesAsync();

        SuccessMessage = "Profile updated successfully.";
        return Page();
    }

    private int GetCurrentUserId()
    {
        return int.Parse(User.FindFirstValue(ClaimTypes.NameIdentifier)!);
    }
}