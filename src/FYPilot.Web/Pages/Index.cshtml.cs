using System.Security.Claims;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace FYPilot.Web.Pages;

public class IndexModel : PageModel
{
    public IActionResult OnGet()
    {
        if (User.Identity?.IsAuthenticated == true)
        {
            var role = User.FindFirst(ClaimTypes.Role)?.Value;
            return role switch
            {
                "supervisor" => RedirectToPage("/Supervisor/Dashboard"),
                "admin"      => RedirectToPage("/Admin/Dashboard"),
                _            => RedirectToPage("/Student/Dashboard"),
            };
        }
        return Page();
    }
}
