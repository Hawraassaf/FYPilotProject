using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Notifications;

[Authorize]
public class IndexModel(
    ApplicationDbContext db) : PageModel
{
    private const int PageSize = 12;

    public IReadOnlyList<NotificationItem> Notifications { get; private set; } =
        Array.Empty<NotificationItem>();

    public IReadOnlyList<ProjectFilterItem> Projects { get; private set; } =
        Array.Empty<ProjectFilterItem>();

    public IReadOnlyList<string> Types { get; private set; } =
        Array.Empty<string>();

    [BindProperty(SupportsGet = true)]
    public string Filter { get; set; } = "all";

    [BindProperty(SupportsGet = true)]
    public int? ProjectId { get; set; }

    [BindProperty(SupportsGet = true)]
    public string? Type { get; set; }

    [BindProperty(SupportsGet = true)]
    public int PageNumber { get; set; } = 1;

    public int TotalCount { get; private set; }

    public int UnreadCount { get; private set; }

    public int TotalPages { get; private set; } = 1;

    [TempData]
    public string? StatusMessage { get; set; }

    public async Task<IActionResult> OnGetAsync(
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (!userId.HasValue)
        {
            return Challenge();
        }

        NormalizeFilters();

        var baseQuery = db.Notifications
            .AsNoTracking()
            .Where(notification =>
                notification.RecipientUserId == userId.Value);

        UnreadCount = await baseQuery
            .CountAsync(
                notification => !notification.IsRead,
                cancellationToken);

        /*
         * PostgreSQL/EF Core cannot reliably translate
         * Distinct + OrderBy after projecting directly
         * into the positional ProjectFilterItem record.
         *
         * Keep the database projection anonymous, finish
         * the query, then create the record objects and
         * order them in memory.
         */
        var projectRows =
            await baseQuery
                .Where(notification =>
                    notification.ProjectId.HasValue &&
                    notification.Project != null)
                .Select(notification => new
                {
                    Id =
                        notification.ProjectId!.Value,

                    Title =
                        notification.Project!.Title
                })
                .Distinct()
                .ToListAsync(
                    cancellationToken);

        Projects =
            projectRows
                .Select(project =>
                    new ProjectFilterItem(
                        project.Id,

                        string.IsNullOrWhiteSpace(
                            project.Title)
                            ? "Untitled Project"
                            : project.Title.Trim()))
                .OrderBy(project =>
                    project.Title)
                .ToList();

        Types = await baseQuery
            .Where(notification => notification.Type != "")
            .Select(notification => notification.Type)
            .Distinct()
            .OrderBy(type => type)
            .ToListAsync(cancellationToken);

        var filteredQuery = ApplyFilters(baseQuery);

        TotalCount = await filteredQuery
            .CountAsync(cancellationToken);

        TotalPages = Math.Max(
            1,
            (int)Math.Ceiling(
                TotalCount / (double)PageSize));

        PageNumber = Math.Clamp(
            PageNumber,
            1,
            TotalPages);

        Notifications = await filteredQuery
            .OrderByDescending(notification => notification.CreatedAt)
            .ThenByDescending(notification => notification.Id)
            .Skip((PageNumber - 1) * PageSize)
            .Take(PageSize)
            .Select(notification =>
                new NotificationItem(
                    notification.Id,
                    notification.ProjectId,
                    notification.Project != null
                        ? notification.Project.Title
                        : null,
                    notification.Title,
                    notification.Message,
                    notification.Type,
                    notification.Url,
                    notification.IsRead,
                    notification.CreatedAt,
                    notification.ReadAt))
            .ToListAsync(cancellationToken);

        return Page();
    }

    public async Task<IActionResult> OnPostOpenAsync(
        int notificationId,
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (!userId.HasValue)
        {
            return Challenge();
        }

        var notification = await db.Notifications
            .FirstOrDefaultAsync(
                item =>
                    item.Id == notificationId &&
                    item.RecipientUserId == userId.Value,
                cancellationToken);

        if (notification == null)
        {
            return NotFound();
        }

        await MarkReadAsync(
            notification,
            cancellationToken);

        if (!string.IsNullOrWhiteSpace(notification.Url) &&
            Url.IsLocalUrl(notification.Url))
        {
            return LocalRedirect(notification.Url);
        }

        StatusMessage =
            "This notification has no available destination.";

        return RedirectToPage();
    }

    public async Task<IActionResult> OnPostMarkReadAsync(
        int notificationId,
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (!userId.HasValue)
        {
            return Challenge();
        }

        var notification = await db.Notifications
            .FirstOrDefaultAsync(
                item =>
                    item.Id == notificationId &&
                    item.RecipientUserId == userId.Value,
                cancellationToken);

        if (notification == null)
        {
            return NotFound();
        }

        await MarkReadAsync(
            notification,
            cancellationToken);

        StatusMessage = "Notification marked as read.";

        return RedirectToCurrentFilters();
    }

    public async Task<IActionResult> OnPostMarkAllReadAsync(
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (!userId.HasValue)
        {
            return Challenge();
        }

        var now = DateTime.UtcNow;

        var updated = await db.Notifications
            .Where(notification =>
                notification.RecipientUserId == userId.Value &&
                !notification.IsRead)
            .ExecuteUpdateAsync(
                setters => setters
                    .SetProperty(
                        notification => notification.IsRead,
                        true)
                    .SetProperty(
                        notification => notification.ReadAt,
                        now),
                cancellationToken);

        StatusMessage = updated == 0
            ? "You have no unread notifications."
            : $"{updated} notification"
              + (updated == 1 ? "" : "s")
              + " marked as read.";

        return RedirectToCurrentFilters();
    }

    private IQueryable<Notification> ApplyFilters(
        IQueryable<Notification> query)
    {
        query = Filter switch
        {
            "unread" => query.Where(notification => !notification.IsRead),
            "read" => query.Where(notification => notification.IsRead),
            _ => query
        };

        if (ProjectId.HasValue)
        {
            query = query.Where(notification =>
                notification.ProjectId == ProjectId.Value);
        }

        if (!string.IsNullOrWhiteSpace(Type))
        {
            query = query.Where(notification =>
                notification.Type == Type);
        }

        return query;
    }

    private async Task MarkReadAsync(
        Notification notification,
        CancellationToken cancellationToken)
    {
        if (notification.IsRead)
        {
            return;
        }

        notification.IsRead = true;
        notification.ReadAt = DateTime.UtcNow;

        await db.SaveChangesAsync(cancellationToken);
    }

    private void NormalizeFilters()
    {
        Filter = Filter?.Trim().ToLowerInvariant() switch
        {
            "unread" => "unread",
            "read" => "read",
            _ => "all"
        };

        Type = string.IsNullOrWhiteSpace(Type)
            ? null
            : Type.Trim().ToLowerInvariant();

        if (ProjectId <= 0)
        {
            ProjectId = null;
        }

        PageNumber = Math.Max(1, PageNumber);
    }

    private IActionResult RedirectToCurrentFilters()
    {
        return RedirectToPage(
            new
            {
                filter = Filter,
                projectId = ProjectId,
                type = Type,
                pageNumber = PageNumber
            });
    }

    private int? CurrentUserId()
    {
        var value = User.FindFirstValue(
            ClaimTypes.NameIdentifier);

        return int.TryParse(value, out var userId)
            ? userId
            : null;
    }

    public sealed record NotificationItem(
        int Id,
        int? ProjectId,
        string? ProjectTitle,
        string Title,
        string Message,
        string Type,
        string Url,
        bool IsRead,
        DateTime CreatedAtUtc,
        DateTime? ReadAtUtc);

    public sealed record ProjectFilterItem(
        int Id,
        string Title);
}