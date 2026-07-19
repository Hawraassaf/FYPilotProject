using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Middleware;

public sealed class ForcePasswordChangeMiddleware
{
    private readonly RequestDelegate _next;

    public ForcePasswordChangeMiddleware(RequestDelegate next)
    {
        _next = next;
    }

    public async Task InvokeAsync(
        HttpContext context,
        ApplicationDbContext db)
    {
        if (context.User.Identity?.IsAuthenticated != true)
        {
            await _next(context);
            return;
        }

        if (IsAllowedPath(context.Request.Path))
        {
            await _next(context);
            return;
        }

        var userIdValue =
            context.User.FindFirst(ClaimTypes.NameIdentifier)?.Value;

        if (!int.TryParse(userIdValue, out var userId))
        {
            await _next(context);
            return;
        }

        var user = await db.Users
            .AsNoTracking()
            .FirstOrDefaultAsync(u => u.Id == userId);

        if (user?.MustChangePassword == true)
        {
            context.Response.Redirect(
                "/Account/ForceChangePassword");

            return;
        }

        await _next(context);
    }

    private static bool IsAllowedPath(PathString path)
    {
        return
            path.StartsWithSegments(
                "/Account/ForceChangePassword") ||

            path.StartsWithSegments(
                "/Account/Logout") ||

            path.StartsWithSegments(
                "/Account/AccessDenied");
    }
}