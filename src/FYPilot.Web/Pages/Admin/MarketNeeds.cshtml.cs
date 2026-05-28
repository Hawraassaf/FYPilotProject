using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class MarketNeedsModel(ApplicationDbContext db) : PageModel
{
    public List<MarketNeed> Needs { get; private set; } = [];
    [BindProperty] public InputModel Input { get; set; } = new();

    public class InputModel
    {
        public string Sector          { get; set; } = "";
        public string Problem         { get; set; } = "";
        public string PossibleSolution{ get; set; } = "";
        public string BusinessValue   { get; set; } = "";
        public int    DemandScore     { get; set; } = 70;
    }

    public async Task OnGetAsync() =>
        Needs = await db.MarketNeeds.OrderByDescending(n => n.DemandScore).ToListAsync();

    public async Task<IActionResult> OnPostAsync()
    {
        db.MarketNeeds.Add(new MarketNeed
        {
            Sector = Input.Sector, Problem = Input.Problem,
            PossibleSolution = Input.PossibleSolution, BusinessValue = Input.BusinessValue,
            DemandScore = Input.DemandScore,
        });
        await db.SaveChangesAsync();
        TempData["Success"] = "Market need added.";
        return RedirectToPage();
    }
}
