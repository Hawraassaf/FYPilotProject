using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace FYPilot.Web.Pages.Account;

/// <summary>
/// Purely informational. No request identifier is accepted here --
/// the applicant has no account and no session tied to this page, so
/// there is nothing to look up or leak.
/// </summary>
public class SupervisorApplicationPendingModel : PageModel
{
    public IActionResult OnGet()
    {
        if (User.Identity?.IsAuthenticated == true)
        {
            return RedirectToPage("/Index");
        }

        return Page();
    }
}
