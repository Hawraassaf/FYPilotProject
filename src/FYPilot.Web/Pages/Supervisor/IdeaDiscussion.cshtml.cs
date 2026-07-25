using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class IdeaDiscussionModel(
    ApplicationDbContext db,
    INotificationService notificationService,
    ILogger<IdeaDiscussionModel> logger)
    : PageModel
{
    public Project? Project { get; private set; }

    public ProjectIdea? Idea { get; private set; }

    public SupervisorEvaluation? Evaluation { get; private set; }

    public List<ProjectMemberItem>
        ProjectMembers
    { get; private set; } = [];

    public List<FeedbackMessageItem>
        Messages
    { get; private set; } = [];

    public string? SuccessMessage { get; private set; }

    public string? ErrorMessage { get; private set; }

    [BindProperty]
    public MessageInputModel MessageInput { get; set; } =
        new();

    public sealed class ProjectMemberItem
    {
        public int UserId { get; set; }

        public string FullName { get; set; } = "";

        public string Email { get; set; } = "";

        public string Role { get; set; } = "";
    }

    public sealed class FeedbackMessageItem
    {
        public int Id { get; set; }

        public int SenderUserId { get; set; }

        public string SenderName { get; set; } = "";

        public string SenderRole { get; set; } = "";

        public bool IsSupervisor { get; set; }

        public string MessageText { get; set; } = "";

        public DateTime CreatedAt { get; set; }

        public DateTime? SeenAt { get; set; }

        public DateTime? EditedAt { get; set; }

        public DateTime? DeletedAt { get; set; }

        public int? ReplyToMessageId { get; set; }

        public string? ReplyPreview { get; set; }
    }

    public sealed class MessageInputModel
    {
        public int ProjectId { get; set; }

        public int IdeaId { get; set; }

        public string? MessageText { get; set; }
    }

    public async Task<IActionResult> OnGetAsync(
        int? projectId,
        int ideaId,
        CancellationToken cancellationToken)
    {
        await LoadDiscussionAsync(
            projectId,
            ideaId,
            cancellationToken);

        if (Project == null ||
            Idea == null)
        {
            TempData["Error"] =
                "The selected project idea was not found "
                + "or the project is not assigned to you.";

            return RedirectToPage(
                "/Supervisor/IdeaReview");
        }

        return Page();
    }

    public async Task<IActionResult>
        OnPostSendMessageAsync(
            CancellationToken cancellationToken)
    {
        var supervisorId =
            SupervisorId();

        if (MessageInput.IdeaId <= 0)
        {
            TempData["Error"] =
                "Select a project idea before sending "
                + "a message.";

            return RedirectToPage(
                "/Supervisor/IdeaReview");
        }

        var cleanMessage =
            MessageInput.MessageText?.Trim() ?? "";

        if (string.IsNullOrWhiteSpace(
                cleanMessage))
        {
            TempData["Error"] =
                "Write a message before sending.";

            return RedirectToPage(
                new
                {
                    projectId =
                        MessageInput.ProjectId,

                    ideaId =
                        MessageInput.IdeaId
                });
        }

        if (cleanMessage.Length > 2000)
        {
            TempData["Error"] =
                "The message cannot exceed "
                + "2000 characters.";

            return RedirectToPage(
                new
                {
                    projectId =
                        MessageInput.ProjectId,

                    ideaId =
                        MessageInput.IdeaId
                });
        }

        var project =
            await LoadAssignedProjectAsync(
                supervisorId,
                MessageInput.ProjectId,
                MessageInput.IdeaId,
                tracked: false,
                cancellationToken);

        if (project?.ProjectIdea == null)
        {
            TempData["Error"] =
                "You can discuss only projects "
                + "officially assigned to you.";

            return RedirectToPage(
                "/Supervisor/IdeaReview");
        }

        var evaluation =
            await GetOrCreateEvaluationAsync(
                project.Id,
                project.ProjectIdea.Id,
                supervisorId,
                cancellationToken);

        db.FeedbackMessages.Add(
            new FeedbackMessage
            {
                EvaluationId =
                    evaluation.Id,

                SenderUserId =
                    supervisorId,

                MessageText =
                    cleanMessage,

                CreatedAt =
                    DateTime.UtcNow
            });

        await db.SaveChangesAsync(
            cancellationToken);

        await NotifyProjectMembersAsync(
            project,
            cancellationToken);

        TempData["Success"] =
            "Message sent successfully.";

        return RedirectToPage(
            new
            {
                projectId =
                    project.Id,

                ideaId =
                    project.ProjectIdea.Id
            });
    }

    private async Task LoadDiscussionAsync(
        int? projectId,
        int ideaId,
        CancellationToken cancellationToken)
    {
        var supervisorId =
            SupervisorId();

        var project =
            await LoadAssignedProjectAsync(
                supervisorId,
                projectId ?? 0,
                ideaId,
                tracked: false,
                cancellationToken);

        if (project?.ProjectIdea == null)
        {
            Project =
                null;

            Idea =
                null;

            Evaluation =
                null;

            ProjectMembers =
                [];

            Messages =
                [];

            return;
        }

        Project =
            project;

        Idea =
            project.ProjectIdea;

        ProjectMembers =
            await LoadProjectMembersAsync(
                project,
                cancellationToken);

        Evaluation =
            await db.SupervisorEvaluations
                .AsNoTracking()
                .FirstOrDefaultAsync(
                    evaluation =>
                        evaluation.ProjectId ==
                            project.Id &&
                        evaluation.IdeaId ==
                            Idea.Id &&
                        evaluation.SupervisorId ==
                            supervisorId,
                    cancellationToken);

        MessageInput =
            new MessageInputModel
            {
                ProjectId =
                    project.Id,

                IdeaId =
                    Idea.Id
            };

        if (Evaluation == null)
        {
            Messages = [];
            return;
        }

        var rawMessages =
            await db.FeedbackMessages
                .AsNoTracking()
                .Where(message =>
                    message.EvaluationId ==
                        Evaluation.Id)
                .OrderBy(message =>
                    message.CreatedAt)
                .ToListAsync(
                    cancellationToken);

        var senderIds =
            rawMessages
                .Select(message =>
                    message.SenderUserId)
                .Distinct()
                .ToList();

        var senders =
            await db.Users
                .AsNoTracking()
                .Where(user =>
                    senderIds.Contains(
                        user.Id))
                .Select(user => new
                {
                    user.Id,
                    user.FullName,
                    user.Role
                })
                .ToDictionaryAsync(
                    user =>
                        user.Id,
                    cancellationToken);

        var messageById =
            rawMessages.ToDictionary(
                message =>
                    message.Id);

        Messages =
            rawMessages
                .Select(message =>
                {
                    FeedbackMessage? replyTarget =
                        null;

                    if (message.ReplyToMessageId.HasValue)
                    {
                        messageById.TryGetValue(
                            message.ReplyToMessageId.Value,
                            out replyTarget);
                    }

                    senders.TryGetValue(
                        message.SenderUserId,
                        out var sender);

                    var isSupervisor =
                        message.SenderUserId ==
                        supervisorId;

                    return new FeedbackMessageItem
                    {
                        Id =
                            message.Id,

                        SenderUserId =
                            message.SenderUserId,

                        SenderName =
                            isSupervisor
                                ? "You"
                                : sender?.FullName
                                  ?? "Project member",

                        SenderRole =
                            isSupervisor
                                ? "Supervisor"
                                : "Student",

                        IsSupervisor =
                            isSupervisor,

                        MessageText =
                            message.MessageText,

                        CreatedAt =
                            message.CreatedAt,

                        SeenAt =
                            message.SeenAt,

                        EditedAt =
                            message.EditedAt,

                        DeletedAt =
                            message.DeletedAt,

                        ReplyToMessageId =
                            message.ReplyToMessageId,

                        ReplyPreview =
                            replyTarget == null ||
                            replyTarget.DeletedAt.HasValue
                                ? null
                                : TrimPreview(
                                    replyTarget.MessageText)
                    };
                })
                .ToList();
    }

    private async Task<Project?>
        LoadAssignedProjectAsync(
            int supervisorId,
            int projectId,
            int ideaId,
            bool tracked,
            CancellationToken cancellationToken)
    {
        IQueryable<Project> query =
            db.Projects
                .Include(project =>
                    project.ProjectIdea)
                .ThenInclude(idea =>
                    idea!.User)
                .Where(project =>
                    project.SupervisorId ==
                        supervisorId &&
                    project.SupervisorAssignmentStatus ==
                        "active" &&
                    project.ProjectIdeaId.HasValue);

        if (!tracked)
        {
            query =
                query.AsNoTracking();
        }

        if (projectId > 0)
        {
            query =
                query.Where(project =>
                    project.Id ==
                        projectId);
        }

        if (ideaId > 0)
        {
            query =
                query.Where(project =>
                    project.ProjectIdeaId ==
                        ideaId);
        }

        return await query
            .FirstOrDefaultAsync(
                cancellationToken);
    }

    private async Task<List<ProjectMemberItem>>
        LoadProjectMembersAsync(
            Project project,
            CancellationToken cancellationToken)
    {
        var members =
            await db.ProjectMembers
                .AsNoTracking()
                .Where(member =>
                    member.ProjectId ==
                        project.Id &&
                    member.Status ==
                        "active")
                .Select(member =>
                    new ProjectMemberItem
                    {
                        UserId =
                            member.UserId,

                        FullName =
                            member.User != null
                                ? member.User.FullName
                                : "Student",

                        Email =
                            member.User != null
                                ? member.User.Email
                                : "",

                        Role =
                            string.IsNullOrWhiteSpace(
                                member.Role)
                                ? "collaborator"
                                : member.Role
                                    .Trim()
                                    .ToLowerInvariant()
                    })
                .ToListAsync(
                    cancellationToken);

        if (members.All(member =>
                member.UserId !=
                    project.StudentId))
        {
            var owner =
                await db.Users
                    .AsNoTracking()
                    .Where(user =>
                        user.Id ==
                            project.StudentId)
                    .Select(user =>
                        new ProjectMemberItem
                        {
                            UserId =
                                user.Id,

                            FullName =
                                user.FullName,

                            Email =
                                user.Email,

                            Role =
                                "owner"
                        })
                    .FirstOrDefaultAsync(
                        cancellationToken);

            if (owner != null)
            {
                members.Add(
                    owner);
            }
        }

        return members
            .GroupBy(member =>
                member.UserId)
            .Select(group =>
                group
                    .OrderByDescending(member =>
                        member.Role == "owner")
                    .First())
            .OrderByDescending(member =>
                member.Role == "owner")
            .ThenBy(member =>
                member.FullName)
            .ToList();
    }

    private async Task<SupervisorEvaluation>
        GetOrCreateEvaluationAsync(
            int projectId,
            int ideaId,
            int supervisorId,
            CancellationToken cancellationToken)
    {
        var evaluation =
            await db.SupervisorEvaluations
                .FirstOrDefaultAsync(
                    item =>
                        item.ProjectId ==
                            projectId &&
                        item.IdeaId ==
                            ideaId,
                    cancellationToken);

        if (evaluation != null)
        {
            return evaluation;
        }

        var now =
            DateTime.UtcNow;

        evaluation =
            new SupervisorEvaluation
            {
                ProjectId =
                    projectId,

                IdeaId =
                    ideaId,

                SupervisorId =
                    supervisorId,

                Status =
                    "pending",

                Comment =
                    "",

                ImprovementSuggestions =
                    "",

                OriginalityScore =
                    0,

                SimilarityScore =
                    0,

                CreatedAt =
                    now,

                UpdatedAt =
                    now
            };

        db.SupervisorEvaluations.Add(
            evaluation);

        try
        {
            await db.SaveChangesAsync(
                cancellationToken);

            return evaluation;
        }
        catch (DbUpdateException exception)
        {
            logger.LogDebug(
                exception,
                "Evaluation creation race for project "
                + "{ProjectId}.",
                projectId);

            db.Entry(evaluation).State =
                EntityState.Detached;

            var existing =
                await db.SupervisorEvaluations
                    .FirstOrDefaultAsync(
                        item =>
                            item.ProjectId ==
                                projectId &&
                            item.IdeaId ==
                                ideaId,
                        cancellationToken);

            if (existing != null)
            {
                return existing;
            }

            throw;
        }
    }

    private async Task NotifyProjectMembersAsync(
        Project project,
        CancellationToken cancellationToken)
    {
        var members =
            await LoadProjectMembersAsync(
                project,
                cancellationToken);

        var projectTitle =
            string.IsNullOrWhiteSpace(
                project.Title)
                ? "Untitled Project"
                : project.Title.Trim();

        foreach (var member in members)
        {
            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId:
                        member.UserId,

                    title:
                        "New Supervisor Feedback Message",

                    message:
                        $"Your supervisor sent a new "
                        + $"message for project "
                        + $"\"{projectTitle}\".",

                    type:
                        "project_feedback_message",

                    url:
                        $"/Student/Feedback"
                        + $"?projectId={project.Id}",

                    sendEmail:
                        false);
            }
            catch (Exception exception)
            {
                logger.LogWarning(
                    exception,
                    "Feedback message was saved but "
                    + "notification failed for project "
                    + "member {UserId}.",
                    member.UserId);
            }
        }
    }

    private static string TrimPreview(
        string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "";
        }

        var clean =
            value.Trim();

        return clean.Length <= 80
            ? clean
            : clean[..80] + "...";
    }

    private int SupervisorId()
    {
        var claim =
            User.FindFirst(
                ClaimTypes.NameIdentifier)
                ?.Value;

        if (int.TryParse(
                claim,
                out var supervisorId))
        {
            return supervisorId;
        }

        throw new InvalidOperationException(
            "Unable to identify the logged-in "
            + "supervisor.");
    }
}