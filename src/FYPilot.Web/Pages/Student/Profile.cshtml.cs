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
public class ProfileModel(ApplicationDbContext db, IWebHostEnvironment env) : PageModel
{
    [BindProperty]
    public ProfileInput Input { get; set; } = new();

    [BindProperty]
    public IFormFile? ProfilePhoto { get; set; }

   
    public string? ErrorMessage { get; set; }

    public string Initials { get; private set; } = "U";
    public string DisplayName { get; private set; } = "Student";
    public string DisplayEmail { get; private set; } = "";
    public string? ProfileImagePath { get; private set; }

    public class ProfileInput
    {
        [Required(ErrorMessage = "Full name is required.")]
        [StringLength(120)]
        public string FullName { get; set; } = "";

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

        public string PreferredDomain { get; set; } = "";

        [StringLength(200)]
        public string PreferredStack { get; set; } = "";

        [Range(1, 60, ErrorMessage = "Available hours must be between 1 and 60.")]
        public int AvailableHoursPerWeek { get; set; } = 20;

        [Range(1, 6, ErrorMessage = "Team members must be between 1 and 6.")]
        public int TeamMembers { get; set; } = 1;

        public string TargetDifficulty { get; set; } = "intermediate";

        [StringLength(1000)]
        public string ProjectGoals { get; set; } = "";
    }

    public async Task<IActionResult> OnGetAsync()
    {
        ErrorMessage = TempData["Error"] as string;

        var userId = GetCurrentUserId();

        var (user, profile) = await GetOrCreateProfileAsync(userId);

        Input = new ProfileInput
        {
            FullName = user.FullName,
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

        LoadDisplayData(user, profile);

        return Page();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        var userId = GetCurrentUserId();

        var (user, profile) = await GetOrCreateProfileAsync(userId);

        if (!ModelState.IsValid)
        {
            ErrorMessage = "Please correct the highlighted fields.";
            LoadDisplayData(user, profile);
            return Page();
        }

        if (ProfilePhoto is { Length: > 0 })
        {
            var uploadResult = await SaveProfilePhotoAsync(userId, profile);

            if (!uploadResult.Success)
            {
                ErrorMessage = uploadResult.Message;
                LoadDisplayData(user, profile);
                return Page();
            }
        }

        user.FullName = Input.FullName.Trim();

        profile.University = Input.University.Trim();
        profile.Major = Input.Major.Trim();
        profile.Year = Input.Year.Trim();
        profile.Interests = Input.Interests.Trim();
        profile.ExperienceLevel = Input.ExperienceLevel.Trim().ToLowerInvariant();

        profile.PreferredDomain = Input.PreferredDomain?.Trim() ?? "";
        profile.PreferredStack = Input.PreferredStack?.Trim() ?? "";
        profile.AvailableHoursPerWeek = Input.AvailableHoursPerWeek;
        profile.TeamMembers = Input.TeamMembers;
        profile.TargetDifficulty = string.IsNullOrWhiteSpace(Input.TargetDifficulty)
            ? "intermediate"
            : Input.TargetDifficulty.Trim().ToLowerInvariant();
        profile.ProjectGoals = Input.ProjectGoals?.Trim() ?? "";

        await db.SaveChangesAsync();

        TempData["Success"] = "Profile updated successfully.";
        return RedirectToPage();
    }

    public async Task<IActionResult> OnPostRemovePhotoAsync()
    {
        var userId = GetCurrentUserId();

        var (user, profile) = await GetOrCreateProfileAsync(userId);

        DeleteOldProfilePhoto(profile.ProfileImagePath);

        profile.ProfileImagePath = null;

        await db.SaveChangesAsync();

        TempData["Success"] = "Profile photo removed successfully.";
        return RedirectToPage();
    }

    private async Task<(User user, StudentProfile profile)> GetOrCreateProfileAsync(int userId)
    {
        var user = await db.Users.FirstOrDefaultAsync(u => u.Id == userId);

        if (user == null)
        {
            throw new InvalidOperationException("Current user was not found.");
        }

        var profile = await db.StudentProfiles
            .FirstOrDefaultAsync(p => p.UserId == userId);

        if (profile == null)
        {
            profile = new StudentProfile
            {
                UserId = userId,
                University = "",
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

        return (user, profile);
    }

    private async Task<(bool Success, string Message)> SaveProfilePhotoAsync(int userId, StudentProfile profile)
    {
        if (ProfilePhoto == null || ProfilePhoto.Length == 0)
        {
            return (true, "");
        }

        const long maxFileSize = 3 * 1024 * 1024;

        if (ProfilePhoto.Length > maxFileSize)
        {
            return (false, "Profile photo must be smaller than 3MB.");
        }

        var extension = Path.GetExtension(ProfilePhoto.FileName).ToLowerInvariant();

        var allowedExtensions = new HashSet<string>
        {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp"
        };

        if (!allowedExtensions.Contains(extension))
        {
            return (false, "Only JPG, PNG, and WEBP profile photos are allowed.");
        }

        var uploadsFolder = Path.Combine(env.WebRootPath, "uploads", "profiles");
        Directory.CreateDirectory(uploadsFolder);

        var fileName = $"student-{userId}-{Guid.NewGuid():N}{extension}";
        var filePath = Path.Combine(uploadsFolder, fileName);

        await using (var stream = System.IO.File.Create(filePath))
        {
            await ProfilePhoto.CopyToAsync(stream);
        }

        DeleteOldProfilePhoto(profile.ProfileImagePath);

        profile.ProfileImagePath = $"/uploads/profiles/{fileName}";

        return (true, "");
    }

    private void DeleteOldProfilePhoto(string? oldPath)
    {
        if (string.IsNullOrWhiteSpace(oldPath))
        {
            return;
        }

        if (!oldPath.StartsWith("/uploads/profiles/", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        var relativePath = oldPath.TrimStart('/').Replace('/', Path.DirectorySeparatorChar);
        var fullPath = Path.Combine(env.WebRootPath, relativePath);

        if (System.IO.File.Exists(fullPath))
        {
            System.IO.File.Delete(fullPath);
        }
    }

    private void LoadDisplayData(User user, StudentProfile profile)
    {
        DisplayName = string.IsNullOrWhiteSpace(user.FullName) ? "Student" : user.FullName;
        DisplayEmail = user.Email;
        ProfileImagePath = profile.ProfileImagePath;
        Initials = BuildInitials(DisplayName);
    }

    private static string BuildInitials(string fullName)
    {
        if (string.IsNullOrWhiteSpace(fullName))
        {
            return "U";
        }

        var parts = fullName.Split(' ', StringSplitOptions.RemoveEmptyEntries);

        if (parts.Length >= 2)
        {
            return $"{parts[0][0]}{parts[1][0]}".ToUpperInvariant();
        }

        return fullName[0].ToString().ToUpperInvariant();
    }

    private int GetCurrentUserId()
    {
        return int.Parse(User.FindFirstValue(ClaimTypes.NameIdentifier)!);
    }
}