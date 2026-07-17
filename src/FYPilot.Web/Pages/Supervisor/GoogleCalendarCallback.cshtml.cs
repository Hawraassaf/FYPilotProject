using System.Security.Claims;
using FYPilot.Web.Services.GoogleCalendar;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class GoogleCalendarCallbackModel(
    IGoogleCalendarService googleCalendar) : PageModel
{
    public async Task<IActionResult> OnGetAsync(
        string? code,
        string? state,
        string? error)
    {
        if (!string.IsNullOrWhiteSpace(error))
        {
            TempData["Error"] =
                $"Google Calendar connection was cancelled: {error}";

            return RedirectToPage("/Supervisor/Meetings");
        }

        var expectedState =
            HttpContext.Session.GetString("GoogleCalendarOAuthState");

        if (string.IsNullOrWhiteSpace(state) ||
            string.IsNullOrWhiteSpace(expectedState) ||
            state != expectedState)
        {
            TempData["Error"] =
                "Google Calendar security validation failed.";

            return RedirectToPage("/Supervisor/Meetings");
        }

        if (string.IsNullOrWhiteSpace(code))
        {
            TempData["Error"] =
                "Google did not return an authorization code.";

            return RedirectToPage("/Supervisor/Meetings");
        }

        var supervisorId = int.Parse(
            User.FindFirstValue(ClaimTypes.NameIdentifier)!);

        await googleCalendar.ConnectAsync(
            supervisorId,
            code);

        HttpContext.Session.Remove("GoogleCalendarOAuthState");

        TempData["Success"] =
            "Google Calendar connected successfully.";

        return RedirectToPage("/Supervisor/Meetings");
    }
}