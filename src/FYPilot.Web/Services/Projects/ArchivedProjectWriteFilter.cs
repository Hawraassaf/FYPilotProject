using System.Globalization;
using System.Reflection;
using System.Security.Claims;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.AspNetCore.Mvc.ViewFeatures;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Services.Projects;

public sealed class ArchivedProjectWriteFilter(
    IProjectAccessService projectAccessService,
    ApplicationDbContext db,
    ITempDataDictionaryFactory tempDataFactory)
    : IAsyncPageFilter
{
    private static readonly HashSet<string>
        ProtectedProjectPages =
            new(StringComparer.OrdinalIgnoreCase)
            {
                "/Student/Dashboard",
                "/Student/IdeaGenerator",
                "/Student/IdeaComparison",
                "/Student/MarketDemand",
                "/Student/ProjectDNA",
                "/Student/ProjectDetails",
                "/Student/Roadmap",
                "/Student/DocumentationGenerator",
                "/Student/SEDocumentation",
                "/Student/MentorChat",
                "/Student/DefenseSimulator",
                "/Student/Feedback",
                "/Student/SupervisorFeedback",
                "/Student/TeamManagement",
                "/Student/ProjectWorkspace"
            };

    private static readonly HashSet<string>
        LifecyclePages =
            new(StringComparer.OrdinalIgnoreCase)
            {
                "/Student/MyProjects",
                "/Student/ArchivedProjects"
            };

    public Task OnPageHandlerSelectionAsync(
        PageHandlerSelectedContext context)
    {
        return Task.CompletedTask;
    }

    public async Task OnPageHandlerExecutionAsync(
        PageHandlerExecutingContext context,
        PageHandlerExecutionDelegate next)
    {
        var request =
            context.HttpContext.Request;

        /*
         * Archived projects remain readable.
         * Only write requests are protected here.
         */
        if (HttpMethods.IsGet(request.Method) ||
            HttpMethods.IsHead(request.Method) ||
            HttpMethods.IsOptions(request.Method))
        {
            await next();
            return;
        }

        if (context.HttpContext.User
            .IsInRole("student") == false)
        {
            await next();
            return;
        }

        var pagePath =
            context.ActionDescriptor.ViewEnginePath
            ?? string.Empty;

        /*
         * My Projects and Archived Projects contain the
         * intentional Archive, Restore, and Remove actions.
         * Their handlers already perform lifecycle checks.
         */
        if (LifecyclePages.Contains(pagePath))
        {
            await next();
            return;
        }

        var userId =
            CurrentUserId(
                context.HttpContext.User);

        if (!userId.HasValue)
        {
            context.Result =
                new RedirectToPageResult(
                    "/Account/Login");

            return;
        }

        var projectId =
            await ResolveProjectIdAsync(
                context,
                pagePath,
                userId.Value,
                context.HttpContext.RequestAborted);

        /*
         * Non-project pages may contain normal POST
         * operations and should continue normally.
         */
        if (!projectId.HasValue)
        {
            if (!ProtectedProjectPages.Contains(
                    pagePath))
            {
                await next();
                return;
            }

            SetError(
                context,
                "Choose a valid project before "
                + "continuing.");

            context.Result =
                new RedirectToPageResult(
                    "/Student/MyProjects");

            return;
        }

        var access =
            await projectAccessService.GetAccessAsync(
                projectId.Value,
                userId.Value,
                "student",
                context.HttpContext.RequestAborted);

        if (access?.CanView != true)
        {
            SetError(
                context,
                "You do not have access to "
                + "that project.");

            context.Result =
                new RedirectToPageResult(
                    "/Student/MyProjects");

            return;
        }

        if (access.CanEdit)
        {
            await next();
            return;
        }

        SetError(
            context,
            "Restore this project before "
            + "making changes.");

        context.Result =
            new RedirectToPageResult(
                "/Student/ArchivedProjects");
    }

    private async Task<int?> ResolveProjectIdAsync(
        PageHandlerExecutingContext context,
        string pagePath,
        int userId,
        CancellationToken cancellationToken)
    {
        /*
         * Handler parameters such as:
         * OnPostSaveAsync(int projectId)
         */
        foreach (var argument
                 in context.HandlerArguments)
        {
            if (!string.Equals(
                    argument.Key,
                    "projectId",
                    StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var argumentProjectId =
                ToPositiveInteger(
                    argument.Value);

            if (argumentProjectId.HasValue)
            {
                return argumentProjectId;
            }
        }

        /*
         * Query string:
         * /Student/Roadmap?projectId=12
         */
        var queryProjectId =
            ToPositiveInteger(
                context.HttpContext.Request
                    .Query["projectId"]);

        if (queryProjectId.HasValue)
        {
            return queryProjectId;
        }

        /*
         * Route value, when a page uses route templates.
         */
        var routeProjectId =
            ToPositiveInteger(
                context.RouteData.Values
                    .GetValueOrDefault("projectId"));

        if (routeProjectId.HasValue)
        {
            return routeProjectId;
        }

        /*
         * Form field:
         * <input name="projectId" ... />
         */
        var request =
            context.HttpContext.Request;

        if (request.HasFormContentType)
        {
            var form =
                await request.ReadFormAsync(
                    cancellationToken);

            var formProjectId =
                ToPositiveInteger(
                    form["projectId"]);

            if (formProjectId.HasValue)
            {
                return formProjectId;
            }
        }

        /*
         * Bound PageModel property:
         * public int ProjectId { get; set; }
         */
        var projectProperty =
            context.HandlerInstance
                ?.GetType()
                .GetProperty(
                    "ProjectId",
                    BindingFlags.Public |
                    BindingFlags.Instance |
                    BindingFlags.IgnoreCase);

        if (projectProperty != null &&
            projectProperty.CanRead)
        {
            var propertyProjectId =
                ToPositiveInteger(
                    projectProperty.GetValue(
                        context.HandlerInstance));

            if (propertyProjectId.HasValue)
            {
                return propertyProjectId;
            }
        }

        /*
         * Some existing project pages rely on the
         * student's most recently active project.
         */
        if (!ProtectedProjectPages.Contains(
                pagePath))
        {
            return null;
        }

        return await db.Users
            .AsNoTracking()
            .Where(user =>
                user.Id == userId)
            .Select(user =>
                user.LastActiveProjectId)
            .FirstOrDefaultAsync(
                cancellationToken);
    }

    private static int? ToPositiveInteger(
        object? value)
    {
        if (value == null)
        {
            return null;
        }

        if (value is int integerValue)
        {
            return integerValue > 0
                ? integerValue
                : null;
        }

        if (value is long longValue &&
            longValue > 0 &&
            longValue <= int.MaxValue)
        {
            return (int)longValue;
        }

        var text =
            Convert.ToString(
                value,
                CultureInfo.InvariantCulture);

        return int.TryParse(
                text,
                NumberStyles.Integer,
                CultureInfo.InvariantCulture,
                out var parsedValue) &&
               parsedValue > 0
            ? parsedValue
            : null;
    }

    private static int? CurrentUserId(
        ClaimsPrincipal user)
    {
        var value =
            user.FindFirst(
                ClaimTypes.NameIdentifier)
                ?.Value;

        return int.TryParse(
            value,
            out var userId)
                ? userId
                : null;
    }

    private void SetError(
        PageHandlerExecutingContext context,
        string message)
    {
        var tempData =
            tempDataFactory.GetTempData(
                context.HttpContext);

        tempData["Error"] =
            message;
    }
}