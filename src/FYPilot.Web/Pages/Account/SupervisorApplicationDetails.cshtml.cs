using System.ComponentModel.DataAnnotations;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using FYPilot.Web.Services.SupervisorRegistration;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Account;

/// <summary>
/// The compact academic-details form a Supervisor applicant fills in
/// after verifying their email. Still no User, no SupervisorProfile,
/// and no authentication cookie exist at this point -- submitting
/// only advances the SupervisorRegistrationRequest to pending_admin
/// for Admin review.
/// </summary>
public class SupervisorApplicationDetailsModel(
    ApplicationDbContext db,
    ISupervisorApplicationTokenService tokenService,
    INotificationService notificationService,
    ILogger<SupervisorApplicationDetailsModel> logger)
    : PageModel
{
    [BindProperty(SupportsGet = true)]
    public string? Token { get; set; }

    [BindProperty]
    public DetailsInputModel Input { get; set; } = new();

    public string? ErrorMessage { get; private set; }

    public class DetailsInputModel
    {
        [Required(ErrorMessage = "Academic title is required.")]
        public string AcademicTitle { get; set; } = "";

        [Required(ErrorMessage = "University / institution is required.")]
        [StringLength(150)]
        public string University { get; set; } = "";

        [Required(ErrorMessage = "Department / faculty is required.")]
        [StringLength(150)]
        public string Department { get; set; } = "";

        [Required(ErrorMessage = "Specialization is required.")]
        [StringLength(200)]
        public string Specialization { get; set; } = "";

        [StringLength(300)]
        [Url(ErrorMessage = "Enter a valid URL.")]
        public string? ProfessionalProfileUrl { get; set; }
    }

    public async Task<IActionResult> OnGetAsync(
        CancellationToken cancellationToken)
    {
        if (User.Identity?.IsAuthenticated == true)
        {
            return RedirectToPage("/Index");
        }

        var request = await LoadEditableRequestAsync(
            cancellationToken);

        if (request == null)
        {
            return RedirectInvalid();
        }

        return Page();
    }

    public async Task<IActionResult> OnPostAsync(
        CancellationToken cancellationToken)
    {
        if (User.Identity?.IsAuthenticated == true)
        {
            return RedirectToPage("/Index");
        }

        var request = await LoadEditableRequestAsync(
            cancellationToken);

        if (request == null)
        {
            return RedirectInvalid();
        }

        if (!ModelState.IsValid)
        {
            ErrorMessage = "Please correct the errors below.";
            return Page();
        }

        request.AcademicTitle = Input.AcademicTitle.Trim();
        request.University = Input.University.Trim();
        request.Department = Input.Department.Trim();
        request.Specialization = Input.Specialization.Trim();
        request.ProfessionalProfileUrl =
            string.IsNullOrWhiteSpace(Input.ProfessionalProfileUrl)
                ? null
                : Input.ProfessionalProfileUrl.Trim();
        request.Status = SupervisorRegistrationStatus.PendingAdmin;
        request.SubmittedAtUtc = DateTime.UtcNow;

        try
        {
            await db.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException exception)
        {
            db.ChangeTracker.Clear();

            logger.LogError(
                exception,
                "Could not submit supervisor application details "
                + "for request {RequestId}.",
                request.Id);

            ErrorMessage =
                "Your application could not be submitted. "
                + "Please try again.";

            return Page();
        }

        await NotifyAdminsAsync(request, cancellationToken);

        return RedirectToPage(
            "/Account/SupervisorApplicationPending");
    }

    private async Task<SupervisorRegistrationRequest?>
        LoadEditableRequestAsync(
            CancellationToken cancellationToken)
    {
        if (!tokenService.TryReadToken(
                Token,
                SupervisorApplicationTokenPurpose.SupervisorDetails,
                out var requestId))
        {
            return null;
        }

        var request = await db.SupervisorRegistrationRequests
            .FirstOrDefaultAsync(
                item => item.Id == requestId,
                cancellationToken);

        if (request == null ||
            request.Status !=
                SupervisorRegistrationStatus.AwaitingDetails ||
            request.VerifiedAtUtc == null)
        {
            return null;
        }

        return request;
    }

    private async Task NotifyAdminsAsync(
        SupervisorRegistrationRequest request,
        CancellationToken cancellationToken)
    {
        List<int> adminIds;

        try
        {
            adminIds = await db.Users
                .AsNoTracking()
                .Where(user => user.Role == "admin")
                .Select(user => user.Id)
                .ToListAsync(cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogWarning(
                exception,
                "Could not load Admin recipients for supervisor "
                + "application {RequestId}.",
                request.Id);

            return;
        }

        foreach (var adminId in adminIds)
        {
            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId: adminId,
                    title: "New Supervisor Registration",
                    message:
                        $"{request.FullName} submitted a verified "
                        + "Supervisor registration request.",
                    type: "supervisor_registration_request",
                    url: "/Admin/SupervisorAssignments",
                    cancellationToken: cancellationToken);
            }
            catch (Exception exception)
            {
                logger.LogWarning(
                    exception,
                    "Could not notify admin {AdminId} about "
                    + "supervisor application {RequestId}.",
                    adminId,
                    request.Id);
            }
        }
    }

    private IActionResult RedirectInvalid()
    {
        TempData["Error"] =
            "This application link is invalid, expired, or already "
            + "used. Please register again.";

        return RedirectToPage("/Account/Register");
    }
}
