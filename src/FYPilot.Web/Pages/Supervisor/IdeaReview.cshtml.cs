using System.Net;
using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class IdeaReviewModel(
    ApplicationDbContext db,
    INotificationService notificationService,
    ILogger<IdeaReviewModel> logger)
    : PageModel
{
    public List<IdeaListItem> AllIdeas { get; private set; } = [];

    public Project? SelectedProject { get; private set; }

    public ProjectIdea? SelectedIdea { get; private set; }

    public SupervisorEvaluation? Evaluation { get; private set; }

    public List<ProjectMemberItem>
        SelectedProjectMembers
    { get; private set; } = [];

    public List<FeedbackMessageItem>
        Messages
    { get; private set; } = [];

    public int TotalIdeas { get; private set; }

    public int PendingReviews { get; private set; }

    public int ReviewedIdeas { get; private set; }

    public int NeedsRevisionIdeas { get; private set; }

    public string? SuccessMessage { get; private set; }

    public string? ErrorMessage { get; private set; }

    [BindProperty]
    public EvaluationInputModel EvaluationInput { get; set; } =
        new();

    [BindProperty]
    public MessageInputModel MessageInput { get; set; } =
        new();

    public sealed class IdeaListItem
    {
        public int Id { get; set; }

        public int ProjectId { get; set; }

        public string ProjectTitle { get; set; } = "";

        public string Title { get; set; } = "";

        /*
         * Kept for compatibility with the existing
         * Razor design. It now contains the project
         * member names rather than one idea creator.
         */
        public string StudentName { get; set; } = "";

        public string ProjectMembers { get; set; } = "";

        public int ProjectMemberCount { get; set; }

        public string? Domain { get; set; }

        public string? DifficultyLevel { get; set; }

        public DateTime CreatedAt { get; set; }

        public int FeasibilityScore { get; set; }

        public string Status { get; set; } = "pending";

        public bool IsSelected { get; set; }
    }

    public sealed class ProjectMemberItem
    {
        public int UserId { get; set; }

        public string FullName { get; set; } = "";

        public string Email { get; set; } = "";

        public string Role { get; set; } = "collaborator";
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

    public sealed class EvaluationInputModel
    {
        public int ProjectId { get; set; }

        public int IdeaId { get; set; }

        public string Status { get; set; } = "pending";

        public string? Comment { get; set; }

        public string? ImprovementSuggestions { get; set; }

        public int? OriginalityScore { get; set; }

        public int? SimilarityScore { get; set; }
    }

    public sealed class MessageInputModel
    {
        public int ProjectId { get; set; }

        public int IdeaId { get; set; }

        public string? MessageText { get; set; }
    }

    public async Task OnGetAsync(
        int? projectId,
        int? ideaId,
        CancellationToken cancellationToken)
    {
        await LoadPageAsync(
            projectId,
            ideaId,
            cancellationToken);
    }

    public async Task<IActionResult>
        OnPostSaveEvaluationAsync(
            CancellationToken cancellationToken)
    {
        var supervisorId =
            SupervisorId();

        var project =
            await LoadAssignedProjectAsync(
                supervisorId,
                EvaluationInput.ProjectId,
                EvaluationInput.IdeaId,
                tracked: true,
                cancellationToken);

        if (project?.ProjectIdea == null)
        {
            TempData["Error"] =
                "This project or its official idea is "
                + "no longer assigned to you.";

            return RedirectToPage();
        }

        var idea =
            project.ProjectIdea;

        var status =
            NormalizeStatus(
                EvaluationInput.Status);

        var comment =
            EvaluationInput.Comment?.Trim() ?? "";

        var suggestions =
            EvaluationInput
                .ImprovementSuggestions
                ?.Trim() ?? "";

        if (comment.Length > 4000)
        {
            TempData["Error"] =
                "The supervisor comment cannot exceed "
                + "4000 characters.";

            return RedirectToSelectedProject(
                project.Id,
                idea.Id);
        }

        if (suggestions.Length > 4000)
        {
            TempData["Error"] =
                "Improvement suggestions cannot exceed "
                + "4000 characters.";

            return RedirectToSelectedProject(
                project.Id,
                idea.Id);
        }

        if (EvaluationInput.OriginalityScore.HasValue &&
            (
                EvaluationInput.OriginalityScore.Value < 0 ||
                EvaluationInput.OriginalityScore.Value > 100
            ))
        {
            TempData["Error"] =
                "Originality score must be between "
                + "0 and 100.";

            return RedirectToSelectedProject(
                project.Id,
                idea.Id);
        }

        if (EvaluationInput.SimilarityScore.HasValue &&
            (
                EvaluationInput.SimilarityScore.Value < 0 ||
                EvaluationInput.SimilarityScore.Value > 100
            ))
        {
            TempData["Error"] =
                "Similarity score must be between "
                + "0 and 100.";

            return RedirectToSelectedProject(
                project.Id,
                idea.Id);
        }

        if (status != "pending" &&
            (
                !EvaluationInput.OriginalityScore.HasValue ||
                !EvaluationInput.SimilarityScore.HasValue
            ))
        {
            TempData["Error"] =
                "Enter both originality and similarity "
                + "scores before saving a final decision.";

            return RedirectToSelectedProject(
                project.Id,
                idea.Id);
        }

        if (status == "approved" &&
            string.IsNullOrWhiteSpace(comment))
        {
            TempData["Error"] =
                "Write a supervisor comment explaining "
                + "why the project idea was approved.";

            return RedirectToSelectedProject(
                project.Id,
                idea.Id);
        }

        if (status == "needs_revision" &&
            string.IsNullOrWhiteSpace(comment))
        {
            TempData["Error"] =
                "Write a comment explaining what the "
                + "project team needs to revise.";

            return RedirectToSelectedProject(
                project.Id,
                idea.Id);
        }

        if (status == "needs_revision" &&
            string.IsNullOrWhiteSpace(suggestions))
        {
            TempData["Error"] =
                "Write improvement suggestions for the "
                + "project team.";

            return RedirectToSelectedProject(
                project.Id,
                idea.Id);
        }

        if (status == "rejected" &&
            string.IsNullOrWhiteSpace(comment))
        {
            TempData["Error"] =
                "Write a clear reason before rejecting "
                + "the project idea.";

            return RedirectToSelectedProject(
                project.Id,
                idea.Id);
        }

        var originalityScore =
            EvaluationInput.OriginalityScore ?? 0;

        var similarityScore =
            EvaluationInput.SimilarityScore ?? 0;

        var now =
            DateTime.UtcNow;

        /*
         * Every submitted evaluation is a new review round.
         *
         * Previous evaluations are never overwritten, so
         * students can open the complete evaluation history.
         */
        var evaluation =
            new SupervisorEvaluation
            {
                ProjectId =
                    project.Id,

                IdeaId =
                    idea.Id,

                SupervisorId =
                    supervisorId,

                Status =
                    status,

                Comment =
                    comment,

                ImprovementSuggestions =
                    suggestions,

                OriginalityScore =
                    originalityScore,

                SimilarityScore =
                    similarityScore,

                CreatedAt =
                    now,

                UpdatedAt =
                    now
            };

        db.SupervisorEvaluations.Add(
            evaluation);

        db.ProjectActivities.Add(
            new ProjectActivity
            {
                ProjectId =
                    project.Id,

                UserId =
                    supervisorId,

                ActionType =
                    "evaluation_created",

                Description =
                    $"A new supervisor evaluation round "
                    + $"was saved with status "
                    + $"\"{status}\".",

                CreatedAtUtc =
                    now
            });

        try
        {
            await db.SaveChangesAsync(
                cancellationToken);
        }
        catch (DbUpdateException exception)
        {
            var postgresException =
                exception.InnerException
                    as PostgresException
                ?? exception.GetBaseException()
                    as PostgresException;

            if (postgresException?.SqlState ==
                PostgresErrorCodes.UniqueViolation)
            {
                logger.LogError(
                    exception,
                    "A legacy unique constraint blocked "
                    + "a new evaluation round. "
                    + "ProjectId: {ProjectId}, "
                    + "IdeaId: {IdeaId}, "
                    + "Constraint: {ConstraintName}.",
                    project.Id,
                    idea.Id,
                    postgresException
                        .ConstraintName);

                TempData["Error"] =
                    "The database still has the old "
                    + "one-evaluation-only constraint. "
                    + "Apply the "
                    + "AllowMultipleSupervisorEvaluations "
                    + "migration, then try again.";
            }
            else
            {
                logger.LogError(
                    exception,
                    "Could not save a new evaluation "
                    + "round. ProjectId: {ProjectId}, "
                    + "IdeaId: {IdeaId}, "
                    + "DatabaseMessage: {DatabaseMessage}.",
                    project.Id,
                    idea.Id,
                    exception.GetBaseException()
                        .Message);

                TempData["Error"] =
                    "The new evaluation could not be "
                    + "saved. Check the application log "
                    + "for the database error.";
            }

            return RedirectToSelectedProject(
                project.Id,
                idea.Id);
        }

        /*
         * Notify every active project member.
         *
         * The owner is added separately only when an
         * older project is missing its owner membership
         * row. Duplicate recipients are removed.
         */
        await NotifyProjectMembersOfEvaluationAsync(
            project,
            evaluation,
            cancellationToken);

        TempData["Success"] =
            "A new evaluation was saved successfully. "
            + "The evaluation form is ready for "
            + "another review round.";

        return RedirectToSelectedProject(
            project.Id,
            idea.Id);
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
                "Select a project before sending "
                + "a message.";

            return RedirectToPage();
        }

        var cleanMessage =
            MessageInput.MessageText?.Trim() ?? "";

        if (string.IsNullOrWhiteSpace(
                cleanMessage))
        {
            TempData["Error"] =
                "Write a message before sending.";

            return RedirectToSelectedProject(
                MessageInput.ProjectId,
                MessageInput.IdeaId);
        }

        if (cleanMessage.Length > 2000)
        {
            TempData["Error"] =
                "The message cannot exceed "
                + "2000 characters.";

            return RedirectToSelectedProject(
                MessageInput.ProjectId,
                MessageInput.IdeaId);
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
                "You can send messages only for projects "
                + "officially assigned to you.";

            return RedirectToPage();
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

        /*
         * A normal feedback reply creates only an
         * in-app notification. Email is reserved for
         * evaluation changes and meeting events.
         */
        await NotifyProjectMembersOfMessageAsync(
            project,
            cancellationToken);

        TempData["Success"] =
            "Message sent successfully.";

        return RedirectToPage(
            "/Supervisor/IdeaDiscussion",
            new
            {
                projectId =
                    project.Id,

                ideaId =
                    project.ProjectIdea.Id
            });
    }

    private async Task LoadPageAsync(
        int? projectId,
        int? ideaId,
        CancellationToken cancellationToken)
    {
        var supervisorId =
            SupervisorId();

        var projects =
            await db.Projects
                .AsNoTracking()
                .Include(project =>
                    project.ProjectIdea)
                .ThenInclude(idea =>
                    idea!.User)
                .Include(project =>
                    project.Members
                        .Where(member =>
                            member.Status ==
                                "active"))
                .ThenInclude(member =>
                    member.User)
                .Where(project =>
                    project.SupervisorId ==
                        supervisorId &&
                    project.SupervisorAssignmentStatus ==
                        "active" &&
                    project.ProjectIdeaId.HasValue)
                .OrderByDescending(project =>
                    project.UpdatedAt)
                .AsSplitQuery()
                .ToListAsync(
                    cancellationToken);

        var allMemberIds =
            projects
                .Select(project =>
                    project.StudentId)
                .Concat(
                    projects
                        .SelectMany(project =>
                            project.Members)
                        .Select(member =>
                            member.UserId))
                .Distinct()
                .ToList();

        var users =
     await db.Users
         .AsNoTracking()
         .Where(user =>
             allMemberIds.Contains(
                 user.Id))
         .Select(user =>
             new UserSummary(
                 user.Id,
                 user.FullName,
                 user.Email))
         .ToListAsync(
             cancellationToken);

        var userMap =
            users.ToDictionary(
                user =>
                    user.UserId);

        var projectIds =
            projects
                .Select(project =>
                    project.Id)
                .ToList();

        var evaluations =
            await db.SupervisorEvaluations
                .AsNoTracking()
                .Where(evaluation =>
                    evaluation.SupervisorId ==
                        supervisorId &&
                    evaluation.ProjectId.HasValue &&
                    projectIds.Contains(
                        evaluation.ProjectId.Value))
                .ToListAsync(
                    cancellationToken);

        var evaluationByProject =
            evaluations
                .GroupBy(evaluation =>
                    evaluation.ProjectId!.Value)
                .ToDictionary(
                    group =>
                        group.Key,

                    group =>
                        group
                            .OrderByDescending(
                                evaluation =>
                                    evaluation.UpdatedAt)
                            .ThenByDescending(
                                evaluation =>
                                    evaluation.Id)
                            .First());

        TotalIdeas =
            projects.Count;

        PendingReviews =
            projects.Count(project =>
                !evaluationByProject.TryGetValue(
                    project.Id,
                    out var evaluation) ||
                NormalizeStatus(
                    evaluation.Status) ==
                    "pending");

        ReviewedIdeas =
            projects.Count(project =>
                evaluationByProject.TryGetValue(
                    project.Id,
                    out var evaluation) &&
                NormalizeStatus(
                    evaluation.Status) !=
                    "pending");

        NeedsRevisionIdeas =
            projects.Count(project =>
                evaluationByProject.TryGetValue(
                    project.Id,
                    out var evaluation) &&
                NormalizeStatus(
                    evaluation.Status) ==
                    "needs_revision");

        var selectedProject =
            ResolveSelectedProject(
                projects,
                projectId,
                ideaId);

        var selectedProjectId =
            selectedProject?.Id;

        AllIdeas =
            projects
                .Where(project =>
                    project.ProjectIdea != null)
                .Select(project =>
                {
                    var members =
                        BuildProjectMembers(
                            project,
                            userMap);

                    evaluationByProject.TryGetValue(
                        project.Id,
                        out var evaluation);

                    return new IdeaListItem
                    {
                        Id =
                            project.ProjectIdea!.Id,

                        ProjectId =
                            project.Id,

                        ProjectTitle =
                            SafeProjectTitle(
                                project.Title),

                        Title =
                            project.ProjectIdea.Title,

                        StudentName =
                            JoinMemberNames(
                                members),

                        ProjectMembers =
                            JoinMemberNames(
                                members),

                        ProjectMemberCount =
                            members.Count,

                        Domain =
                            project.ProjectIdea.Domain,

                        DifficultyLevel =
                            project.ProjectIdea
                                .DifficultyLevel,

                        CreatedAt =
                            project.ProjectIdea.CreatedAt,

                        FeasibilityScore =
                            project.ProjectIdea
                                .FeasibilityScore,

                        Status =
                            evaluation?.Status
                            ?? "pending",

                        IsSelected =
                            selectedProjectId.HasValue &&
                            project.Id ==
                                selectedProjectId.Value
                    };
                })
                .ToList();

        if (selectedProject?.ProjectIdea == null)
        {
            SelectedProject =
                null;

            SelectedIdea =
                null;

            Evaluation =
                null;

            SelectedProjectMembers =
                [];

            Messages =
                [];

            EvaluationInput =
                new EvaluationInputModel();

            MessageInput =
                new MessageInputModel();

            return;
        }

        SelectedProject =
            selectedProject;

        SelectedIdea =
            selectedProject.ProjectIdea;

        SelectedProjectMembers =
            BuildProjectMembers(
                selectedProject,
                userMap);

        evaluationByProject.TryGetValue(
            selectedProject.Id,
            out var selectedEvaluation);

        Evaluation =
            selectedEvaluation;

        /*
         * Evaluation keeps the latest saved review for the
         * status shown in the project queue.
         *
         * EvaluationInput is deliberately reset so the
         * supervisor starts a fresh evaluation round.
         */
        EvaluationInput =
            new EvaluationInputModel
            {
                ProjectId =
                    selectedProject.Id,

                IdeaId =
                    SelectedIdea.Id,

                Status =
                    "pending",

                Comment =
                    "",

                ImprovementSuggestions =
                    "",

                OriginalityScore =
                    null,

                SimilarityScore =
                    null
            };

        MessageInput =
            new MessageInputModel
            {
                ProjectId =
                    selectedProject.Id,

                IdeaId =
                    SelectedIdea.Id
            };

        await LoadMessagesAsync(
            supervisorId,
            Evaluation,
            cancellationToken);
    }

    private async Task LoadMessagesAsync(
        int supervisorId,
        SupervisorEvaluation? evaluation,
        CancellationToken cancellationToken)
    {
        if (evaluation == null)
        {
            Messages = [];
            return;
        }

        var rawMessages =
            await db.FeedbackMessages
                .AsNoTracking()
                .Where(message =>
                    message.EvaluationId ==
                        evaluation.Id)
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
                    project.ProjectIdea);

        if (!tracked)
        {
            query =
                query.AsNoTracking();
        }

        query =
            query.Where(project =>
                project.SupervisorId ==
                    supervisorId &&
                project.SupervisorAssignmentStatus ==
                    "active" &&
                project.ProjectIdeaId.HasValue);

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

    private async Task<SupervisorEvaluation>
        GetOrCreateEvaluationAsync(
            int projectId,
            int ideaId,
            int supervisorId,
            CancellationToken cancellationToken)
    {
        /*
         * Discussion messages belong to the latest
         * evaluation round. Never select an older round
         * randomly when several evaluations exist.
         */
        var evaluation =
            await db.SupervisorEvaluations
                .Where(item =>
                    item.ProjectId ==
                        projectId &&
                    item.IdeaId ==
                        ideaId &&
                    item.SupervisorId ==
                        supervisorId)
                .OrderByDescending(item =>
                    item.UpdatedAt)
                .ThenByDescending(item =>
                    item.Id)
                .FirstOrDefaultAsync(
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
        catch (DbUpdateException)
        {
            db.Entry(evaluation).State =
                EntityState.Detached;

            var existing =
                await db.SupervisorEvaluations
                    .Where(item =>
                        item.ProjectId ==
                            projectId &&
                        item.IdeaId ==
                            ideaId &&
                        item.SupervisorId ==
                            supervisorId)
                    .OrderByDescending(item =>
                        item.UpdatedAt)
                    .ThenByDescending(item =>
                        item.Id)
                    .FirstOrDefaultAsync(
                        cancellationToken);

            if (existing != null)
            {
                return existing;
            }

            throw;
        }
    }

    private async Task
        NotifyProjectMembersOfEvaluationAsync(
            Project project,
            SupervisorEvaluation evaluation,
            CancellationToken cancellationToken)
    {
        var recipients =
            await LoadProjectRecipientsAsync(
                project.Id,
                project.StudentId,
                cancellationToken);

        var supervisorName =
            await db.Users
                .AsNoTracking()
                .Where(user =>
                    user.Id ==
                        evaluation.SupervisorId)
                .Select(user =>
                    user.FullName)
                .FirstOrDefaultAsync(
                    cancellationToken)
            ?? "Your supervisor";

        var projectTitle =
            SafeProjectTitle(
                project.Title);

        var ideaTitle =
            project.ProjectIdea?.Title
            ?? "Official project idea";

        var statusLabel =
            EvaluationStatusLabel(
                evaluation.Status);

        foreach (var recipient in recipients)
        {
            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId:
                        recipient.UserId,

                    title:
                        "New Project Evaluation",

                    message:
                        $"{supervisorName} added a new "
                        + $"evaluation for project "
                        + $"\"{projectTitle}\". "
                        + $"Status: {statusLabel}.",

                    type:
                        "project_evaluation_created",

                    url:
                        $"/Student/Feedback"
                        + $"?projectId={project.Id}"
                        + $"&evaluationId={evaluation.Id}",

                    sendEmail:
                        true,

                    emailSubject:
                        $"New evaluation for "
                        + projectTitle,

                    emailHtmlBody:
                        BuildEvaluationEmail(
                            recipient.FullName,
                            supervisorName,
                            projectTitle,
                            ideaTitle,
                            statusLabel,
                            evaluation.Comment,
                            evaluation
                                .ImprovementSuggestions,
                            evaluation.OriginalityScore,
                            evaluation.SimilarityScore));
            }
            catch (Exception exception)
            {
                logger.LogWarning(
                    exception,
                    "Evaluation was saved but "
                    + "notification failed for user "
                    + "{UserId} in project {ProjectId}.",
                    recipient.UserId,
                    project.Id);
            }
        }
    }

    private async Task
        NotifyProjectMembersOfMessageAsync(
            Project project,
            CancellationToken cancellationToken)
    {
        var recipients =
            await LoadProjectRecipientsAsync(
                project.Id,
                project.StudentId,
                cancellationToken);

        var projectTitle =
            SafeProjectTitle(
                project.Title);

        foreach (var recipient in recipients)
        {
            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId:
                        recipient.UserId,

                    title:
                        "New Supervisor Feedback Message",

                    message:
                        $"Your supervisor sent a message "
                        + $"for project "
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
                    + "notification failed for user "
                    + "{UserId}.",
                    recipient.UserId);
            }
        }
    }

    private async Task<List<ProjectRecipient>>
        LoadProjectRecipientsAsync(
            int projectId,
            int ownerId,
            CancellationToken cancellationToken)
    {
        var recipients =
            await db.ProjectMembers
                .AsNoTracking()
                .Where(member =>
                    member.ProjectId ==
                        projectId &&
                    member.Status ==
                        "active")
                .Select(member =>
                    new ProjectRecipient(
                        member.UserId,
                        member.User != null
                            ? member.User.FullName
                            : "Student"))
                .ToListAsync(
                    cancellationToken);

        if (!recipients.Any(
                recipient =>
                    recipient.UserId ==
                        ownerId))
        {
            var owner =
                await db.Users
                    .AsNoTracking()
                    .Where(user =>
                        user.Id ==
                            ownerId)
                    .Select(user =>
                        new ProjectRecipient(
                            user.Id,
                            user.FullName))
                    .FirstOrDefaultAsync(
                        cancellationToken);

            if (owner != null)
            {
                recipients.Add(
                    owner);
            }
        }

        return recipients
            .GroupBy(recipient =>
                recipient.UserId)
            .Select(group =>
                group.First())
            .ToList();
    }

    private static Project? ResolveSelectedProject(
        List<Project> projects,
        int? projectId,
        int? ideaId)
    {
        if (projectId.HasValue)
        {
            var byProject =
                projects.FirstOrDefault(
                    project =>
                        project.Id ==
                            projectId.Value);

            if (byProject != null)
            {
                return byProject;
            }
        }

        if (ideaId.HasValue)
        {
            var byIdea =
                projects.FirstOrDefault(
                    project =>
                        project.ProjectIdeaId ==
                            ideaId.Value);

            if (byIdea != null)
            {
                return byIdea;
            }
        }

        return projects.FirstOrDefault();
    }

    private static List<ProjectMemberItem>
        BuildProjectMembers(
            Project project,
            IReadOnlyDictionary<int, UserSummary> userMap)
    {
        var members =
            project.Members
                .Where(member =>
                    member.Status ==
                        "active")
                .Select(member =>
                {
                    userMap.TryGetValue(
                        member.UserId,
                        out var user);

                    return new ProjectMemberItem
                    {
                        UserId =
                            member.UserId,

                        FullName =
                            user?.FullName
                            ?? member.User?.FullName
                            ?? "Student",

                        Email =
                            user?.Email
                            ?? member.User?.Email
                            ?? "",

                        Role =
                            string.IsNullOrWhiteSpace(
                                member.Role)
                                ? "collaborator"
                                : member.Role
                                    .Trim()
                                    .ToLowerInvariant()
                    };
                })
                .ToList();

        if (members.All(member =>
                member.UserId !=
                    project.StudentId))
        {
            userMap.TryGetValue(
                project.StudentId,
                out var owner);

            members.Add(
                new ProjectMemberItem
                {
                    UserId =
                        project.StudentId,

                    FullName =
                        owner?.FullName
                        ?? "Project owner",

                    Email =
                        owner?.Email
                        ?? "",

                    Role =
                        "owner"
                });
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

    private static string JoinMemberNames(
        IEnumerable<ProjectMemberItem> members)
    {
        return string.Join(
            ", ",
            members.Select(member =>
                member.FullName));
    }

    private IActionResult RedirectToSelectedProject(
        int projectId,
        int ideaId)
    {
        return RedirectToPage(
            new
            {
                projectId,
                ideaId
            });
    }

    private static string BuildEvaluationEmail(
        string recipientName,
        string supervisorName,
        string projectTitle,
        string ideaTitle,
        string status,
        string comment,
        string suggestions,
        int originalityScore,
        int similarityScore)
    {
        return $"""
        <div style="font-family:Arial,sans-serif;
                    background:#F7F8FA;
                    padding:28px;">
            <div style="max-width:660px;
                        margin:auto;
                        background:#ffffff;
                        border:1px solid #E8ECF2;
                        border-radius:18px;
                        padding:26px;">
                <h2 style="color:#28385E;
                           margin-top:0;">
                    New Project Evaluation
                </h2>

                <p style="color:#475569;
                          line-height:1.7;">
                    Hello {Encode(recipientName)},
                </p>

                <p style="color:#475569;
                          line-height:1.7;">
                    {Encode(supervisorName)} added a new
                    evaluation for
                    <strong>{Encode(projectTitle)}</strong>.
                </p>

                <div style="background:#F7F8FA;
                            border:1px solid #E8ECF2;
                            border-radius:14px;
                            padding:14px;
                            color:#28385E;
                            line-height:1.8;">
                    <strong>Official idea:</strong>
                    {Encode(ideaTitle)}<br/>

                    <strong>Status:</strong>
                    {Encode(status)}<br/>

                    <strong>Originality:</strong>
                    {originalityScore}%<br/>

                    <strong>Similarity:</strong>
                    {similarityScore}%
                </div>

                <p style="color:#475569;
                          line-height:1.7;
                          margin-top:18px;">
                    <strong>Supervisor comment:</strong><br/>
                    {Encode(
                        string.IsNullOrWhiteSpace(comment)
                            ? "No comment provided."
                            : comment)}
                </p>

                <p style="color:#475569;
                          line-height:1.7;">
                    <strong>Improvement suggestions:</strong><br/>
                    {Encode(
                        string.IsNullOrWhiteSpace(suggestions)
                            ? "No suggestions provided."
                            : suggestions)}
                </p>

                <p style="color:#94A3B8;
                          font-size:12px;
                          margin-top:24px;">
                    FYPilot automated notification
                </p>
            </div>
        </div>
        """;
    }

    private static string EvaluationStatusLabel(
        string? status)
    {
        return NormalizeStatus(status) switch
        {
            "approved" =>
                "Approved",

            "needs_revision" =>
                "Needs Revision",

            "rejected" =>
                "Rejected",

            _ =>
                "Pending Review"
        };
    }

    private static string NormalizeStatus(
        string? status)
    {
        var normalized =
            string.IsNullOrWhiteSpace(status)
                ? "pending"
                : status.Trim()
                    .ToLowerInvariant();

        return normalized switch
        {
            "approved" =>
                "approved",

            "needs_revision" =>
                "needs_revision",

            "rejected" =>
                "rejected",

            _ =>
                "pending"
        };
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

    private static string SafeProjectTitle(
        string? title)
    {
        return string.IsNullOrWhiteSpace(title)
            ? "Untitled Project"
            : title.Trim();
    }

    private static string Encode(
        string? value)
    {
        return WebUtility.HtmlEncode(
            value ?? "");
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
    private sealed record UserSummary(
    int UserId,
    string FullName,
    string Email);

    private sealed record ProjectRecipient(
        int UserId,
        string FullName);
}