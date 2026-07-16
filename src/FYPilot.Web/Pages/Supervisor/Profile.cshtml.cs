using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class ProfileModel(ApplicationDbContext db, IWebHostEnvironment env) : PageModel
{
    [BindProperty]
    public ProfileInput Input { get; set; } = new();

    [BindProperty]
    public IFormFile? ProfilePhoto { get; set; }

    public string? SuccessMessage { get; set; }
    public string? ErrorMessage { get; set; }

    public string Initials { get; private set; } = "S";
    public string DisplayName { get; private set; } = "Supervisor";
    public string DisplayEmail { get; private set; } = "";
    public string? ProfileImagePath { get; private set; }

    public class ProfileInput
    {
        [Required(ErrorMessage = "Full name is required.")]
        [StringLength(120)]
        public string FullName { get; set; } = "";

        [StringLength(100)]
        public string AcademicTitle { get; set; } = "";

        [StringLength(150)]
        public string Department { get; set; } = "";

        [StringLength(150)]
        public string Faculty { get; set; } = "";

        [StringLength(150)]
        public string University { get; set; } = "";

        [StringLength(200)]
        public string Specialization { get; set; } = "";

        [StringLength(700)]
        public string ResearchAreas { get; set; } = "";

        [StringLength(150)]
        public string OfficeLocation { get; set; } = "";

        [StringLength(150)]
        public string OfficeHours { get; set; } = "";

        [StringLength(80)]
        public string PreferredMeetingMode { get; set; } = "";

        [StringLength(1200)]
        public string Bio { get; set; } = "";

        [StringLength(300)]
        public string LinkedInUrl { get; set; } = "";

        [StringLength(300)]
        public string WebsiteUrl { get; set; } = "";
    }

    public async Task<IActionResult> OnGetAsync()
    {
        TempData.Remove("Success");
        TempData.Remove("Error");

        SuccessMessage = TempData["ProfileSuccess"] as string;

        var userId = GetCurrentUserId();

        var (user, profile) = await GetOrCreateProfileAsync(userId);

        Input = new ProfileInput
        {
            FullName = user.FullName,
            AcademicTitle = profile.AcademicTitle ?? "",
            Department = profile.Department ?? "",
            Faculty = profile.Faculty ?? "",
            University = profile.University ?? "",
            Specialization = profile.Specialization ?? "",
            ResearchAreas = profile.ResearchAreas ?? "",
            OfficeLocation = profile.OfficeLocation ?? "",
            OfficeHours = profile.OfficeHours ?? "",
            PreferredMeetingMode = profile.PreferredMeetingMode ?? "",
            Bio = profile.Bio ?? "",
            LinkedInUrl = profile.LinkedInUrl ?? "",
            WebsiteUrl = profile.WebsiteUrl ?? ""
        };

        LoadDisplayData(user, profile);

        return Page();
    }

    public async Task<IActionResult> OnPostAsync()

    {
        var userId = GetCurrentUserId();

        var (user, profile) = await GetOrCreateProfileAsync(userId);

       
        ModelState.Remove("Input.Faculty");
        ModelState.Remove("Input.University");
        ModelState.Remove("Input.LinkedInUrl");
        ModelState.Remove("Input.WebsiteUrl");

        if (!ModelState.IsValid)
        {
            var errors = ModelState
                .Where(x => x.Value?.Errors.Count > 0)
                .SelectMany(x => x.Value!.Errors.Select(e => $"{x.Key}: {e.ErrorMessage}"))
                .ToList();

            ErrorMessage = errors.Any()
                ? string.Join(" | ", errors)
                : "Please correct the highlighted fields.";

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

        profile.AcademicTitle = string.IsNullOrWhiteSpace(Input.AcademicTitle)
     ? "Supervisor"
     : Input.AcademicTitle.Trim();

        profile.Department = string.IsNullOrWhiteSpace(Input.Department)
            ? "Computer Science"
            : Input.Department.Trim();

        profile.Faculty = Clean(Input.Faculty);
        profile.University = Clean(Input.University);

        profile.Specialization = string.IsNullOrWhiteSpace(Input.Specialization)
            ? "General Software Engineering"
            : Input.Specialization.Trim();

        profile.ResearchAreas = string.IsNullOrWhiteSpace(Input.ResearchAreas)
            ? "Software Engineering"
            : Input.ResearchAreas.Trim();

        profile.OfficeLocation = Clean(Input.OfficeLocation);
        profile.OfficeHours = Clean(Input.OfficeHours);

        profile.PreferredMeetingMode = string.IsNullOrWhiteSpace(Input.PreferredMeetingMode)
            ? "Online"
            : Input.PreferredMeetingMode.Trim();

        profile.Bio = Clean(Input.Bio);
        profile.LinkedInUrl = Clean(Input.LinkedInUrl);
        profile.WebsiteUrl = Clean(Input.WebsiteUrl);
        profile.UpdatedAt = DateTime.UtcNow;

        await db.SaveChangesAsync();

        TempData["ProfileSuccess"] = "Profile updated successfully.";
        return RedirectToPage();
    }

    public async Task<IActionResult> OnPostRemovePhotoAsync()
    {
        var userId = GetCurrentUserId();

        var (user, profile) = await GetOrCreateProfileAsync(userId);

        DeleteOldProfilePhoto(profile.ProfileImagePath);

        profile.ProfileImagePath = null;
        profile.UpdatedAt = DateTime.UtcNow;

        await db.SaveChangesAsync();

        TempData["ProfileSuccess"] = "Profile photo removed successfully.";
        return RedirectToPage();
    }

    private async Task<(User user, SupervisorProfile profile)> GetOrCreateProfileAsync(int userId)
    {
        var user = await db.Users.FirstOrDefaultAsync(u => u.Id == userId && u.Role == "supervisor");

        if (user == null)
        {
            throw new InvalidOperationException("Current supervisor user was not found.");
        }

        var profile = await db.SupervisorProfiles
            .FirstOrDefaultAsync(p => p.UserId == userId);

        if (profile == null)
        {
            profile = new SupervisorProfile
            {
                UserId = userId,
                AcademicTitle = "Supervisor",
                Department = "Computer Science",
                Specialization = "General Software Engineering",
                ResearchAreas = "Software Engineering",
                PreferredMeetingMode = "Online",
                Bio = "",
                UpdatedAt = DateTime.UtcNow
            };

            db.SupervisorProfiles.Add(profile);
            await db.SaveChangesAsync();
        }

        return (user, profile);
    }

    private async Task<(bool Success, string Message)> SaveProfilePhotoAsync(int userId, SupervisorProfile profile)
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

        var fileName = $"supervisor-{userId}-{Guid.NewGuid():N}{extension}";
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

    private void LoadDisplayData(User user, SupervisorProfile profile)
    {
        DisplayName = string.IsNullOrWhiteSpace(user.FullName) ? "Supervisor" : user.FullName;
        DisplayEmail = user.Email;
        ProfileImagePath = profile.ProfileImagePath;
        Initials = BuildInitials(DisplayName);
    }

    private static string BuildInitials(string fullName)
    {
        if (string.IsNullOrWhiteSpace(fullName))
        {
            return "S";
        }

        var parts = fullName.Split(' ', StringSplitOptions.RemoveEmptyEntries);

        if (parts.Length >= 2)
        {
            return $"{parts[0][0]}{parts[1][0]}".ToUpperInvariant();
        }

        return fullName[0].ToString().ToUpperInvariant();
    }

    private static string? Clean(string? value)
    {
        return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
    }

    private int GetCurrentUserId()
    {
        return int.Parse(User.FindFirstValue(ClaimTypes.NameIdentifier)!);
    }
}
