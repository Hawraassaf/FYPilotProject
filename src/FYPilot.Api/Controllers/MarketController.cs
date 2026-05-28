using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/market")]
[Authorize]
public class MarketController(ApplicationDbContext db) : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> GetNeeds([FromQuery] string? sector)
    {
        var query = db.MarketNeeds.AsQueryable();
        if (!string.IsNullOrEmpty(sector))
            query = query.Where(m => m.Sector == sector);
        var needs = await query.OrderBy(m => m.Sector).ToListAsync();
        return Ok(needs.Select(n => new MarketNeedResponse(n.Id, n.Sector, n.Problem, n.PossibleSolution, n.BusinessValue, n.DemandScore)));
    }

    [HttpGet("sectors")]
    public IActionResult GetSectors()
    {
        var sectors = new[] { "Healthcare", "Education", "Banking", "Delivery", "E-Commerce", "NGOs", "Small Businesses", "Restaurants", "Universities", "Logistics", "Real Estate", "Fashion", "Clinics" };
        return Ok(sectors);
    }
}
