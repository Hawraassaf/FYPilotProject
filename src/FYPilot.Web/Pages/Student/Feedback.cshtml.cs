using System.Security.Claims;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;
using Npgsql;
namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class FeedbackModel(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService,
    INotificationService notificationService,
    ILogger<FeedbackModel> logger)
    : PageModel
{
    /*
     * Capacity now represents supervised projects,
     * not separate students.
     */
    private const int SupervisorLimit = 4;

    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    public int CurrentUserId { get; private set; }

    public Project? CurrentProject { get; private set; }

    public User? CurrentSupervisor { get; private set; }

    public SupervisorAssignment?
        CurrentAssignment
    { get; private set; }

    public List<ProjectOption>
        AccessibleProjects
    { get; private set; } = [];

    public List<SupervisorOption>
        AvailableSupervisors
    { get; private set; } = [];

    public List<FeedbackItem>
        Feedbacks
    { get; private set; } = [];

    public FeedbackItem?
        SelectedFeedback
    { get; private set; }

    public bool HasProjects =>
        AccessibleProjects.Count > 0;

    public bool IsProjectOwner { get; private set; }

    public string CurrentProjectRole { get; private set; } =
        "collaborator";

    public string AssignmentStatus =>
        NormalizeStatus(
            CurrentProject
                ?.SupervisorAssignmentStatus);

    public bool CanAccessFeedback =>
        CurrentProject != null &&
        CurrentProject.SupervisorId.HasValue &&
        AssignmentStatus == "active";

    public bool CanRequestSupervisor =>
        CurrentProject != null &&
        IsProjectOwner &&
        !CurrentProject.SupervisorId.HasValue &&
        AssignmentStatus != "pending_admin" &&
        AssignmentStatus != "active";

    [BindProperty]
    public int SelectedEvaluationId { get; set; }

    [BindProperty]
    public string ReplyText { get; set; } = "";

    [TempData]
    public string? SuccessMessage { get; set; }

    [TempData]
    public string? ErrorMessage { get; set; }

    public sealed record ProjectOption(
        int Id,
        string Title,
        string IdeaTitle,
        string Role,
        bool IsOwner,
        string SupervisorStatus);

    public sealed record FeedbackItem(
        ProjectIdea Idea,
        SupervisorEvaluation Evaluation,
        string SupervisorName,
        List<FeedbackMessage> Messages);

    private sealed record ProjectMatchContext(
        string Major,
        string Domain,
        string Interests,
        string Skills,
        string Technologies);

    public class SupervisorOption
    {
        public int SupervisorId { get; set; }

        public string FullName { get; set; } = "";

        public string AcademicTitle { get; set; } = "";

        public string Department { get; set; } = "";

        public string Faculty { get; set; } = "";

        public string Specialization { get; set; } = "";

        public string ResearchAreas { get; set; } = "";

        public string OfficeHours { get; set; } = "";

        public string MeetingMode { get; set; } = "";

        public int CapacityUsed { get; set; }

        public int CapacityLimit { get; set; } =
            SupervisorLimit;

        public int MatchScore { get; set; }

        public string MatchReason { get; set; } = "";
    }

    public async Task<IActionResult> OnGetAsync(
        int? evaluationId,
        CancellationToken cancellationToken)
    {
        CurrentUserId =
            GetCurrentUserId();

        await LoadAccessibleProjectsAsync(
            cancellationToken);

        if (!HasProjects)
        {
            Feedbacks = [];
            SelectedFeedback = null;
            AvailableSupervisors = [];

            return Page();
        }

        if (!AccessibleProjects.Any(
                project =>
                    project.Id == ProjectId))
        {
            ProjectId =
                await ResolveDefaultProjectIdAsync(
                    cancellationToken);
        }

        if (ProjectId <= 0)
        {
            Feedbacks = [];
            SelectedFeedback = null;

            return Page();
        }

        if (!await LoadProjectContextAsync(
                cancellationToken))
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        if (CanAccessFeedback)
        {
            await LoadFeedbackAsync(
                evaluationId,
                cancellationToken);
        }
        else
        {
            Feedbacks = [];
            SelectedFeedback = null;

            if (CanRequestSupervisor)
            {
                await LoadAvailableSupervisorsAsync(
                    cancellationToken);
            }
            else
            {
                AvailableSupervisors = [];
            }
        }

        return Page();
    }

    public async Task<IActionResult>
        OnPostRequestSupervisorAsync(
            int supervisorId,
            int? projectId,
            string? studentMessage,
            CancellationToken cancellationToken)
    {
        CurrentUserId =
            GetCurrentUserId();

        ProjectId =
            await ResolvePostedProjectIdAsync(
                projectId,
                cancellationToken);

        if (ProjectId <= 0)
        {
            ErrorMessage =
                "Choose a project before requesting "
                + "a supervisor.";

            return RedirectToPage();
        }

        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                CurrentUserId,
                "student",
                cancellationToken);

        if (access == null)
        {
            ErrorMessage =
                "You do not have access to this project.";

            return RedirectToPage();
        }

        /*
         * Only the project owner controls the
         * supervisor request.
         *
         * Collaborators automatically inherit the
         * supervisor selected for the project.
         */
        if (!access.IsOwner)
        {
            ErrorMessage =
                "Only the project owner can request "
                + "or change the project supervisor.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var project =
            await db.Projects
                .Include(item =>
                    item.ProjectIdea)
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == ProjectId,
                    cancellationToken);

        if (project == null)
        {
            ErrorMessage =
                "The selected project was not found.";

            return RedirectToPage();
        }

        if (project.SupervisorId.HasValue &&
            NormalizeStatus(
                project.SupervisorAssignmentStatus) ==
            "active")
        {
            ErrorMessage =
                "This project already has an "
                + "official supervisor.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var existingPendingOrActive =
            await db.SupervisorAssignments
                .AnyAsync(
                    assignment =>
                        assignment.ProjectId ==
                            ProjectId &&
                        (
                            assignment.Status ==
                                "pending_admin" ||
                            assignment.Status ==
                                "active"
                        ),
                    cancellationToken);

        if (existingPendingOrActive)
        {
            ErrorMessage =
                "This project already has a pending "
                + "or active supervisor request.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var supervisor =
            await db.Users
                .Include(user =>
                    user.SupervisorProfile)
                .FirstOrDefaultAsync(
                    user =>
                        user.Id == supervisorId &&
                        user.Role.ToLower() ==
                            "supervisor",
                    cancellationToken);

        if (supervisor == null ||
            supervisor.SupervisorProfile == null)
        {
            ErrorMessage =
                "The selected supervisor was not found.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var cleanStudentMessage =
            studentMessage?.Trim() ?? "";

        if (cleanStudentMessage.Length > 1000)
        {
            ErrorMessage =
                "The request message cannot exceed "
                + "1000 characters.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var occupiedProjectIds =
            await LoadOccupiedProjectIdsAsync(
                supervisorId,
                cancellationToken);

        if (occupiedProjectIds.Count >=
            SupervisorLimit)
        {
            ErrorMessage =
                "This supervisor has reached the "
                + "maximum number of supervised projects. "
                + "Please choose another supervisor.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var now =
            DateTime.UtcNow;

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                cancellationToken);

        try
        {
            var assignment =
                new SupervisorAssignment
                {
                    ProjectId =
                        ProjectId,

                    /*
                     * StudentId is preserved temporarily
                     * as the owner who submitted the
                     * project request.
                     */
                    StudentId =
                        CurrentUserId,

                    SupervisorId =
                        supervisorId,

                    Status =
                        "pending_admin",

                    StudentMessage =
                        cleanStudentMessage,

                    /*
                     * These columns already exist in the
                     * legacy assignment table.
                     *
                     * admin_note is NOT NULL in PostgreSQL,
                     * so it must receive an empty value
                     * while the request is still waiting
                     * for an administrator.
                     */
                    AssignedByAdminId =
                        null,

                    AdminNote =
                        "",

                    RequestedAt =
                        now,

                    ApprovedAt =
                        null,

                    RejectedAt =
                        null,

                    UpdatedAt =
                        now
                };

            db.SupervisorAssignments.Add(
                assignment);

            project.SupervisorId =
                null;

            project.SupervisorAssignmentStatus =
                "pending_admin";

            project.UpdatedAt =
                now;

            /*
             * Save only the critical assignment and
             * project status inside the transaction.
             *
             * Activity logging is non-critical and must
             * never roll back a valid supervisor request.
             */
            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            try
            {
                var activity =
                    new ProjectActivity
                    {
                        ProjectId =
                            ProjectId,

                        UserId =
                            CurrentUserId,

                        ActionType =
                            "supervisor_requested",

                        Description =
                            $"Supervisor request submitted "
                            + $"for {supervisor.FullName}.",

                        CreatedAtUtc =
                            now
                    };

                db.ProjectActivities.Add(
                    activity);

                await db.SaveChangesAsync(
                    cancellationToken);
            }
            catch (Exception activityException)
            {
                logger.LogWarning(
                    activityException,
                    "Supervisor request was saved, but "
                    + "project activity logging failed "
                    + "for project {ProjectId}.",
                    ProjectId);
            }

            /*
             * The requested supervisor is deliberately
             * not notified yet.
             *
             * Admin approval must happen first.
             */
            await NotifyProjectMembersOfRequestAsync(
                project,
                supervisor.FullName,
                cancellationToken);

            await NotifyAdminsOfRequestAsync(
                project,
                supervisor.FullName,
                cancellationToken);

            SuccessMessage =
                "The supervisor request was submitted "
                + "for this project. Please wait for "
                + "admin approval.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }
        catch (DbUpdateException exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            var postgresException =
                exception.InnerException as PostgresException
                ?? exception.GetBaseException()
                    as PostgresException;

            if (postgresException != null)
            {
                logger.LogError(
                    exception,
                    "Supervisor request database failure. "
                    + "ProjectId: {ProjectId}, "
                    + "SqlState: {SqlState}, "
                    + "Message: {Message}, "
                    + "Detail: {Detail}, "
                    + "Constraint: {Constraint}",
                    ProjectId,
                    postgresException.SqlState,
                    postgresException.MessageText,
                    postgresException.Detail,
                    postgresException.ConstraintName);

                ErrorMessage =
                    "Database error: "
                    + postgresException.MessageText
                    + " | SQLSTATE: "
                    + postgresException.SqlState
                    + (
                        string.IsNullOrWhiteSpace(
                            postgresException.ConstraintName)
                            ? ""
                            : " | Constraint: "
                              + postgresException.ConstraintName
                    );
            }
            else
            {
                var databaseMessage =
                    exception.GetBaseException().Message;

                logger.LogError(
                    exception,
                    "Supervisor request database failure "
                    + "for project {ProjectId}: "
                    + "{DatabaseMessage}",
                    ProjectId,
                    databaseMessage);

                ErrorMessage =
                    "Database error: "
                    + databaseMessage;
            }

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to submit supervisor request "
                + "for project {ProjectId} by user "
                + "{UserId}.",
                ProjectId,
                CurrentUserId);

            ErrorMessage =
                "The supervisor request could not be "
                + "submitted.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }
    }

    public async Task<IActionResult>
        OnPostReplyAsync(
            int? projectId,
            CancellationToken cancellationToken)
    {
        CurrentUserId =
            GetCurrentUserId();

        ProjectId =
            await ResolvePostedProjectIdAsync(
                projectId,
                cancellationToken);

        if (ProjectId <= 0)
        {
            ErrorMessage =
                "Choose a project before replying.";

            return RedirectToPage();
        }

        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                CurrentUserId,
                "student",
                cancellationToken);

        if (access == null)
        {
            ErrorMessage =
                "You no longer have access to this project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        var project =
            await db.Projects
                .AsNoTracking()
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == ProjectId,
                    cancellationToken);

        if (project == null ||
            !project.SupervisorId.HasValue ||
            NormalizeStatus(
                project.SupervisorAssignmentStatus) !=
            "active")
        {
            ErrorMessage =
                "You can reply after the project "
                + "supervisor is approved.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        if (SelectedEvaluationId <= 0)
        {
            ErrorMessage =
                "Select a feedback item before replying.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var cleanReply =
            ReplyText?.Trim() ?? "";

        if (string.IsNullOrWhiteSpace(
                cleanReply))
        {
            ErrorMessage =
                "Write a reply before sending.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId,
                    evaluationId =
                        SelectedEvaluationId
                });
        }

        if (cleanReply.Length > 2000)
        {
            ErrorMessage =
                "The reply cannot exceed "
                + "2000 characters.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId,
                    evaluationId =
                        SelectedEvaluationId
                });
        }

        var evaluation =
            await db.SupervisorEvaluations
                .Include(item =>
                    item.Idea)
                .FirstOrDefaultAsync(
                    item =>
                        item.Id ==
                            SelectedEvaluationId &&
                        item.ProjectId ==
                            ProjectId &&
                        item.SupervisorId ==
                            project.SupervisorId &&
                        (
                            !project.ProjectIdeaId
                                .HasValue ||
                            item.IdeaId ==
                                project.ProjectIdeaId
                        ),
                    cancellationToken);

        if (evaluation == null)
        {
            ErrorMessage =
                "This feedback does not belong to "
                + "the selected project.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var message =
            new FeedbackMessage
            {
                EvaluationId =
                    evaluation.Id,

                SenderUserId =
                    CurrentUserId,

                MessageText =
                    cleanReply,

                CreatedAt =
                    DateTime.UtcNow
            };

        db.FeedbackMessages.Add(
            message);

        await db.SaveChangesAsync(
            cancellationToken);

        /*
         * Feedback chat messages create an in-app
         * notification only.
         *
         * Sending an email for every chat reply would
         * create excessive duplicate communication.
         */
        try
        {
            var studentName =
                await db.Users
                    .AsNoTracking()
                    .Where(user =>
                        user.Id ==
                            CurrentUserId)
                    .Select(user =>
                        user.FullName)
                    .FirstOrDefaultAsync(
                        cancellationToken)
                ?? "A project member";

            await notificationService.NotifyUserAsync(
                recipientUserId:
                    project.SupervisorId.Value,

                title:
                    "New project feedback reply",

                message:
                    $"{studentName} replied to feedback "
                    + $"for project "
                    + $"\"{SafeProjectTitle(project.Title)}\".",

                type:
                    "project_feedback_reply",

                url:
                    $"/Supervisor/IdeaDiscussion"
                    + $"?projectId={ProjectId}"
                    + $"&ideaId={evaluation.IdeaId}",

                sendEmail:
                    false);
        }
        catch (Exception exception)
        {
            logger.LogWarning(
                exception,
                "Feedback reply was saved but the "
                + "supervisor notification failed for "
                + "evaluation {EvaluationId}.",
                evaluation.Id);
        }

        SuccessMessage =
            "Reply sent successfully.";

        return RedirectToPage(
            new
            {
                projectId = ProjectId,
                evaluationId =
                    SelectedEvaluationId
            });
    }

    private async Task LoadAccessibleProjectsAsync(
     CancellationToken cancellationToken)
    {
        /*
         * Load the database entities first.
         *
         * Custom C# methods such as NormalizeStatus cannot
         * be translated into PostgreSQL SQL.
         */
        var projectEntities =
            await db.Projects
                .AsNoTracking()
                .Include(project =>
                    project.ProjectIdea)
                .Include(project =>
                    project.Members.Where(member =>
                        member.Status == "active"))
                .Where(project =>
                    project.StudentId ==
                        CurrentUserId ||
                    project.Members.Any(member =>
                        member.UserId ==
                            CurrentUserId &&
                        member.Status ==
                            "active"))
                .OrderBy(project =>
                    project.Title)
                .AsSplitQuery()
                .ToListAsync(
                    cancellationToken);

        /*
         * The query has now finished.
         *
         * Everything below runs in C# memory, so calling
         * NormalizeStatus and using StringComparison is safe.
         */
        AccessibleProjects =
            projectEntities
                .Select(project =>
                {
                    var currentMembership =
                        project.Members
                            .FirstOrDefault(member =>
                                member.UserId ==
                                    CurrentUserId &&
                                member.Status ==
                                    "active");

                    var isOwner =
                        project.StudentId ==
                            CurrentUserId ||
                        string.Equals(
                            currentMembership?.Role,
                            "owner",
                            StringComparison
                                .OrdinalIgnoreCase);

                    var role =
                        isOwner
                            ? "owner"
                            : string.IsNullOrWhiteSpace(
                                currentMembership?.Role)
                                ? "collaborator"
                                : currentMembership.Role;

                    return new ProjectOption(
                        project.Id,

                        string.IsNullOrWhiteSpace(
                            project.Title)
                            ? "Untitled Project"
                            : project.Title,

                        project.ProjectIdea == null
                            ? "No official idea selected"
                            : project.ProjectIdea.Title,

                        role,

                        isOwner,

                        NormalizeStatus(
                            project
                                .SupervisorAssignmentStatus));
                })
                .OrderBy(project =>
                    project.Title)
                .ToList();
    }

    private async Task<int>
        ResolveDefaultProjectIdAsync(
            CancellationToken cancellationToken)
    {
        var lastActiveProjectId =
            await activeProjectService
                .GetActiveProjectIdAsync(
                    CurrentUserId,
                    cancellationToken);

        if (lastActiveProjectId.HasValue &&
            AccessibleProjects.Any(
                project =>
                    project.Id ==
                        lastActiveProjectId.Value))
        {
            return lastActiveProjectId.Value;
        }

        return AccessibleProjects
            .FirstOrDefault()
            ?.Id ?? 0;
    }

    private async Task<int>
        ResolvePostedProjectIdAsync(
            int? postedProjectId,
            CancellationToken cancellationToken)
    {
        if (postedProjectId.HasValue &&
            postedProjectId.Value > 0)
        {
            return postedProjectId.Value;
        }

        if (ProjectId > 0)
        {
            return ProjectId;
        }

        var lastActiveProjectId =
            await activeProjectService
                .GetActiveProjectIdAsync(
                    CurrentUserId,
                    cancellationToken);

        return lastActiveProjectId ?? 0;
    }

    private async Task<bool>
        LoadProjectContextAsync(
            CancellationToken cancellationToken)
    {
        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                CurrentUserId,
                "student",
                cancellationToken);

        if (access == null)
        {
            return false;
        }

        IsProjectOwner =
            access.IsOwner;

        CurrentProjectRole =
            access.AccessRole;

        CurrentProject =
            await db.Projects
                .Include(project =>
                    project.ProjectIdea)
                .Include(project =>
                    project.Supervisor)
                .ThenInclude(supervisor =>
                    supervisor!
                        .SupervisorProfile)
                .Include(project =>
                    project.Members
                        .Where(member =>
                            member.Status ==
                                "active"))
                .ThenInclude(member =>
                    member.User)
                .AsSplitQuery()
                .FirstOrDefaultAsync(
                    project =>
                        project.Id ==
                            ProjectId,
                    cancellationToken);

        if (CurrentProject == null)
        {
            return false;
        }

        CurrentSupervisor =
            CurrentProject.Supervisor;

        CurrentAssignment =
            await db.SupervisorAssignments
                .Include(assignment =>
                    assignment.Supervisor)
                .ThenInclude(supervisor =>
                    supervisor!
                        .SupervisorProfile)
                .Where(assignment =>
                    assignment.ProjectId ==
                        ProjectId)
                .OrderByDescending(
                    assignment =>
                        assignment.UpdatedAt)
                .ThenByDescending(
                    assignment =>
                        assignment.RequestedAt)
                .FirstOrDefaultAsync(
                    cancellationToken);

        await activeProjectService
            .RememberPageAsync(
                CurrentUserId,
                ProjectId,
                "/Student/Feedback",
                cancellationToken);

        return true;
    }

    private async Task LoadAvailableSupervisorsAsync(
        CancellationToken cancellationToken)
    {
        if (CurrentProject == null ||
            !IsProjectOwner)
        {
            AvailableSupervisors = [];
            return;
        }

        var matchContext =
            await BuildProjectMatchContextAsync(
                cancellationToken);

        var activeProjectPairs =
            await db.Projects
                .AsNoTracking()
                .Where(project =>
                    project.SupervisorId.HasValue &&
                    project
                        .SupervisorAssignmentStatus ==
                        "active")
                .Select(project => new
                {
                    SupervisorId =
                        project.SupervisorId!.Value,

                    ProjectId =
                        project.Id
                })
                .ToListAsync(
                    cancellationToken);

        var pendingProjectPairs =
            await db.SupervisorAssignments
                .AsNoTracking()
                .Where(assignment =>
                    assignment.ProjectId.HasValue &&
                    assignment.Status ==
                        "pending_admin")
                .Select(assignment => new
                {
                    assignment.SupervisorId,

                    ProjectId =
                        assignment.ProjectId!.Value
                })
                .ToListAsync(
                    cancellationToken);

        var loadMap =
            activeProjectPairs
                .Concat(
                    pendingProjectPairs)
                .GroupBy(item =>
                    item.SupervisorId)
                .ToDictionary(
                    group =>
                        group.Key,

                    group =>
                        group
                            .Select(item =>
                                item.ProjectId)
                            .Distinct()
                            .Count());

        var supervisors =
            await db.Users
                .AsNoTracking()
                .Include(user =>
                    user.SupervisorProfile)
                .Where(user =>
                    user.Role.ToLower() ==
                        "supervisor" &&
                    user.SupervisorProfile != null)
                .ToListAsync(
                    cancellationToken);

        AvailableSupervisors =
            supervisors
                .Where(supervisor =>
                    loadMap.GetValueOrDefault(
                        supervisor.Id,
                        0) <
                    SupervisorLimit)
                .Select(supervisor =>
                {
                    var profile =
                        supervisor.SupervisorProfile!;

                    var match =
                        CalculateMatch(
                            matchContext,
                            profile);

                    return new SupervisorOption
                    {
                        SupervisorId =
                            supervisor.Id,

                        FullName =
                            supervisor.FullName,

                        AcademicTitle =
                            profile.AcademicTitle ?? "",

                        Department =
                            profile.Department ?? "",

                        Faculty =
                            profile.Faculty ?? "",

                        Specialization =
                            profile.Specialization ?? "",

                        ResearchAreas =
                            profile.ResearchAreas ?? "",

                        OfficeHours =
                            profile.OfficeHours ?? "",

                        MeetingMode =
                            profile
                                .PreferredMeetingMode
                            ?? "",

                        CapacityUsed =
                            loadMap.GetValueOrDefault(
                                supervisor.Id,
                                0),

                        CapacityLimit =
                            SupervisorLimit,

                        MatchScore =
                            match.Score,

                        MatchReason =
                            match.Reason
                    };
                })
                .OrderByDescending(
                    supervisor =>
                        supervisor.MatchScore)
                .ThenBy(
                    supervisor =>
                        supervisor.CapacityUsed)
                .ThenBy(
                    supervisor =>
                        supervisor.FullName)
                .ToList();
    }

    private async Task<ProjectMatchContext>
        BuildProjectMatchContextAsync(
            CancellationToken cancellationToken)
    {
        if (CurrentProject == null)
        {
            return new ProjectMatchContext(
                "",
                "",
                "",
                "",
                "");
        }

        var memberIds =
            CurrentProject.Members
                .Where(member =>
                    member.Status ==
                        "active")
                .Select(member =>
                    member.UserId)
                .Distinct()
                .ToList();

        if (!memberIds.Contains(
                CurrentProject.StudentId))
        {
            memberIds.Add(
                CurrentProject.StudentId);
        }

        var profiles =
            await db.StudentProfiles
                .AsNoTracking()
                .Where(profile =>
                    memberIds.Contains(
                        profile.UserId))
                .ToListAsync(
                    cancellationToken);

        var idea =
            CurrentProject.ProjectIdea;

        return new ProjectMatchContext(
            Major:
                JoinValues(
                    profiles.Select(
                        profile =>
                            profile.Major)),

            Domain:
                JoinValues(
                    new[]
                    {
                        idea?.Domain,
                        idea?.SupervisorCategory
                    }
                    .Concat(
                        profiles.Select(
                            profile =>
                                profile
                                    .PreferredDomain))),

            Interests:
                JoinValues(
                    profiles.Select(
                        profile =>
                            profile.Interests)),

            Skills:
                JoinValues(
                    profiles.Select(
                        profile =>
                            profile.Skills)),

            Technologies:
                JoinValues(
                    new[]
                    {
                        idea?.RequiredTechnologies,
                        idea?.RequiredSkills,
                        CurrentProject.Technologies
                    }
                    .Concat(
                        profiles.Select(
                            profile =>
                                profile
                                    .PreferredStack))));
    }

    private async Task LoadFeedbackAsync(
        int? evaluationId,
        CancellationToken cancellationToken)
    {
        if (CurrentProject == null ||
            !CurrentProject.SupervisorId.HasValue ||
            !CurrentProject.ProjectIdeaId.HasValue)
        {
            Feedbacks = [];
            SelectedFeedback = null;
            return;
        }

        var evaluations =
            await db.SupervisorEvaluations
                .AsNoTracking()
                .Where(evaluation =>
                    evaluation.ProjectId ==
                        ProjectId &&
                    evaluation.IdeaId ==
                        CurrentProject.ProjectIdeaId &&
                    evaluation.SupervisorId ==
                        CurrentProject.SupervisorId)
                .Include(evaluation =>
                    evaluation.Idea)
                .Include(evaluation =>
                    evaluation.Supervisor)
                .OrderByDescending(
                    evaluation =>
                        evaluation.UpdatedAt)
                .ToListAsync(
                    cancellationToken);

        var evaluationIds =
            evaluations
                .Select(evaluation =>
                    evaluation.Id)
                .ToList();

        var messages =
            evaluationIds.Count == 0
                ? []
                : await db.FeedbackMessages
                    .AsNoTracking()
                    .Where(message =>
                        evaluationIds.Contains(
                            message.EvaluationId))
                    .OrderBy(message =>
                        message.CreatedAt)
                    .ToListAsync(
                        cancellationToken);

        Feedbacks =
            evaluations
                .Where(evaluation =>
                    evaluation.Idea != null)
                .Select(evaluation =>
                    new FeedbackItem(
                        evaluation.Idea!,

                        evaluation,

                        evaluation.Supervisor
                            ?.FullName
                        ?? CurrentSupervisor
                            ?.FullName
                        ?? "Supervisor",

                        messages
                            .Where(message =>
                                message.EvaluationId ==
                                    evaluation.Id)
                            .ToList()))
                .ToList();

        if (Feedbacks.Count == 0)
        {
            SelectedFeedback =
                null;

            return;
        }

        SelectedFeedback =
            evaluationId.HasValue
                ? Feedbacks.FirstOrDefault(
                    item =>
                        item.Evaluation.Id ==
                            evaluationId.Value)
                : Feedbacks.FirstOrDefault();

        SelectedFeedback ??=
            Feedbacks.First();

        SelectedEvaluationId =
            SelectedFeedback.Evaluation.Id;
    }

    private async Task<HashSet<int>>
        LoadOccupiedProjectIdsAsync(
            int supervisorId,
            CancellationToken cancellationToken)
    {
        var activeProjectIds =
            await db.Projects
                .AsNoTracking()
                .Where(project =>
                    project.SupervisorId ==
                        supervisorId &&
                    project
                        .SupervisorAssignmentStatus ==
                        "active")
                .Select(project =>
                    project.Id)
                .ToListAsync(
                    cancellationToken);

        var pendingProjectIds =
            await db.SupervisorAssignments
                .AsNoTracking()
                .Where(assignment =>
                    assignment.SupervisorId ==
                        supervisorId &&
                    assignment.ProjectId.HasValue &&
                    assignment.Status ==
                        "pending_admin")
                .Select(assignment =>
                    assignment.ProjectId!.Value)
                .ToListAsync(
                    cancellationToken);

        return activeProjectIds
            .Concat(
                pendingProjectIds)
            .ToHashSet();
    }

    private async Task
        NotifyProjectMembersOfRequestAsync(
            Project project,
            string supervisorName,
            CancellationToken cancellationToken)
    {
        var memberIds =
            await db.ProjectMembers
                .AsNoTracking()
                .Where(member =>
                    member.ProjectId ==
                        project.Id &&
                    member.Status ==
                        "active")
                .Select(member =>
                    member.UserId)
                .Distinct()
                .ToListAsync(
                    cancellationToken);

        if (!memberIds.Contains(
                project.StudentId))
        {
            memberIds.Add(
                project.StudentId);
        }

        foreach (var memberId in memberIds)
        {
            if (memberId ==
                CurrentUserId)
            {
                continue;
            }

            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId:
                        memberId,

                    title:
                        "Project supervisor requested",

                    message:
                        $"A request for {supervisorName} "
                        + $"was submitted for project "
                        + $"\"{SafeProjectTitle(project.Title)}\". "
                        + "It is waiting for admin approval.",

                    type:
                        "project_supervisor_requested",

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
                    "Could not notify project member "
                    + "{UserId} about supervisor request "
                    + "for project {ProjectId}.",
                    memberId,
                    project.Id);
            }
        }
    }

    private async Task NotifyAdminsOfRequestAsync(
        Project project,
        string supervisorName,
        CancellationToken cancellationToken)
    {
        var adminIds =
            await db.Users
                .AsNoTracking()
                .Where(user =>
                    user.Role.ToLower() ==
                        "admin")
                .Select(user =>
                    user.Id)
                .ToListAsync(
                    cancellationToken);

        foreach (var adminId in adminIds)
        {
            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId:
                        adminId,

                    title:
                        "New project supervisor request",

                    message:
                        $"Project "
                        + $"\"{SafeProjectTitle(project.Title)}\" "
                        + $"requested {supervisorName}.",

                    type:
                        "project_supervisor_request",

                    url:
                        "/Admin/SupervisorAssignments",

                    sendEmail:
                        false);
            }
            catch (Exception exception)
            {
                logger.LogWarning(
                    exception,
                    "Could not notify admin {AdminId} "
                    + "about project {ProjectId} "
                    + "supervisor request.",
                    adminId,
                    project.Id);
            }
        }
    }

    private static (int Score, string Reason)
        CalculateMatch(
            ProjectMatchContext context,
            SupervisorProfile profile)
    {
        var score =
            10;

        var reasons =
            new List<string>();

        var department =
            profile.Department ?? "";

        var faculty =
            profile.Faculty ?? "";

        var specialization =
            profile.Specialization ?? "";

        var researchAreas =
            profile.ResearchAreas ?? "";

        if (ContainsAny(
                department,
                context.Major) ||
            ContainsAny(
                faculty,
                context.Major) ||
            ContainsAny(
                specialization,
                context.Major) ||
            ContainsAny(
                researchAreas,
                context.Major))
        {
            score +=
                30;

            reasons.Add(
                "matches the team’s academic field");
        }

        if (ContainsAny(
                specialization,
                context.Domain) ||
            ContainsAny(
                researchAreas,
                context.Domain))
        {
            score +=
                30;

            reasons.Add(
                "matches the project domain");
        }

        if (HasAnySharedToken(
                context.Interests,
                researchAreas) ||
            HasAnySharedToken(
                context.Interests,
                specialization))
        {
            score +=
                15;

            reasons.Add(
                "matches team interests");
        }

        if (HasAnySharedToken(
                context.Skills,
                researchAreas) ||
            HasAnySharedToken(
                context.Technologies,
                researchAreas) ||
            HasAnySharedToken(
                context.Skills,
                specialization) ||
            HasAnySharedToken(
                context.Technologies,
                specialization))
        {
            score +=
                15;

            reasons.Add(
                "matches project technologies");
        }

        score =
            Math.Clamp(
                score,
                0,
                100);

        return (
            score,
            reasons.Count == 0
                ? "available for this project"
                : string.Join(
                    ", ",
                    reasons.Distinct()));
    }

    private static bool ContainsAny(
        string source,
        string value)
    {
        if (string.IsNullOrWhiteSpace(
                source) ||
            string.IsNullOrWhiteSpace(
                value))
        {
            return false;
        }

        var sourceTokens =
            SplitTokens(
                source);

        var valueTokens =
            SplitTokens(
                value);

        return sourceTokens.Any(
            sourceToken =>
                valueTokens.Any(
                    valueToken =>
                        sourceToken.Contains(
                            valueToken,
                            StringComparison
                                .OrdinalIgnoreCase) ||
                        valueToken.Contains(
                            sourceToken,
                            StringComparison
                                .OrdinalIgnoreCase)));
    }

    private static bool HasAnySharedToken(
        string left,
        string right)
    {
        if (string.IsNullOrWhiteSpace(
                left) ||
            string.IsNullOrWhiteSpace(
                right))
        {
            return false;
        }

        var leftTokens =
            SplitTokens(
                left);

        var rightTokens =
            SplitTokens(
                right);

        return leftTokens.Intersect(
                rightTokens,
                StringComparer.OrdinalIgnoreCase)
            .Any();
    }

    private static List<string> SplitTokens(
        string value)
    {
        return value
            .Split(
                new[]
                {
                    ',',
                    ';',
                    '/',
                    '|'
                },
                StringSplitOptions
                    .RemoveEmptyEntries)
            .Select(token =>
                token.Trim())
            .Where(token =>
                token.Length >= 2)
            .Distinct(
                StringComparer
                    .OrdinalIgnoreCase)
            .ToList();
    }

    private static string JoinValues(
        IEnumerable<string?> values)
    {
        return string.Join(
            ", ",
            values
                .Where(value =>
                    !string.IsNullOrWhiteSpace(
                        value))
                .Select(value =>
                    value!.Trim())
                .Distinct(
                    StringComparer
                        .OrdinalIgnoreCase));
    }

    private static string NormalizeStatus(
        string? status)
    {
        return string.IsNullOrWhiteSpace(
            status)
                ? "unassigned"
                : status
                    .Trim()
                    .ToLowerInvariant();
    }

    private static string SafeProjectTitle(
        string? title)
    {
        return string.IsNullOrWhiteSpace(
            title)
                ? "Untitled Project"
                : title.Trim();
    }

    private int GetCurrentUserId()
    {
        var claim =
            User.FindFirst(
                ClaimTypes.NameIdentifier)
                ?.Value;

        if (int.TryParse(
                claim,
                out var userId))
        {
            return userId;
        }

        throw new InvalidOperationException(
            "Unable to identify the logged-in user.");
    }
}