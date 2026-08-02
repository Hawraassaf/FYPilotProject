using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Middleware;

public sealed class ForcePasswordChangeMiddleware
{
    private readonly RequestDelegate _next;

    public ForcePasswordChangeMiddleware(
        RequestDelegate next)
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

        var userIdValue =
            context.User.FindFirst(
                ClaimTypes.NameIdentifier)?.Value;

        if (!int.TryParse(
                userIdValue,
                out var userId))
        {
            await SignOutAndRedirectAsync(
                context,
                "session_invalid");

            return;
        }

        /*
         * Validate the account on every authenticated
         * request. A deleted administrator's existing
         * cookie is rejected immediately on the next
         * request.
         */
        var currentUser = await db.Users
            .AsNoTracking()
            .Where(user => user.Id == userId)
            .Select(user => new
            {
                user.Role,
                user.MustChangePassword
            })
            .FirstOrDefaultAsync(
                context.RequestAborted);

        if (currentUser == null)
        {
            await SignOutAndRedirectAsync(
                context,
                "account_deleted");

            return;
        }

        var claimedRole =
            context.User.FindFirst(
                ClaimTypes.Role)?.Value;

        if (!string.Equals(
                claimedRole,
                currentUser.Role,
                StringComparison.OrdinalIgnoreCase))
        {
            await SignOutAndRedirectAsync(
                context,
                "session_invalid");

            return;
        }

        if (IsAllowedPath(
                context.Request.Path))
        {
            await _next(context);
            return;
        }

        if (currentUser.MustChangePassword)
        {
            context.Response.Redirect(
                "/Account/ForceChangePassword");

            return;
        }

        await _next(context);
    }

    private static async Task
        SignOutAndRedirectAsync(
            HttpContext context,
            string reason)
    {
        await context.SignOutAsync(
            CookieAuthenticationDefaults.AuthenticationScheme);

        context.Response.Redirect(
            "/Account/Login?reason="
            + Uri.EscapeDataString(reason));
    }

    private static bool IsAllowedPath(
        PathString path)
    {
        return
            path.StartsWithSegments(
                "/Account/ForceChangePassword")
            ||
            path.StartsWithSegments(
                "/Account/Logout")
            ||
            path.StartsWithSegments(
                "/Account/AccessDenied");
    }
}