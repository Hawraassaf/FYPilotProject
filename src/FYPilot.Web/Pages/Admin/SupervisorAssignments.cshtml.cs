using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;
using FYPilot.Web.Services.Notifications;
namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class SupervisorAssignmentsModel(
    ApplicationDbContext db,
    INotificationService notificationService) : PageModel
{
    private const int SupervisorLimit = 4;

    public AdminAssignmentStats Stats { get; private set; } = new();

    public List<PendingAssignmentRow> PendingRequests { get; private set; } = [];

    public List<SupervisorCapacityRow> SupervisorCapacity { get; private set; } = [];

    public List<ActiveAssignmentRow> ActiveAssignments { get; private set; } = [];

    public List<RejectedAssignmentRow> RecentRejectedRequests { get; private set; } = [];

    public List<AssignableSupervisorOption> AssignableSupervisors { get; private set; } = [];

    [TempData]
    public string? SuccessMessage { get; set; }

    [TempData]
    public string? ErrorMessage { get; set; }

    public async Task OnGetAsync()
    {
        await LoadAsync();
    }

    public async Task<IActionResult> OnPostApproveAsync(int assignmentId)
    {
        var adminId = GetCurrentUserId();

        // Read without tracking because the final update will be atomic.
        var assignment = await db.SupervisorAssignments
            .AsNoTracking()
            .FirstOrDefaultAsync(a =>
                a.Id == assignmentId &&
                a.Status == "pending_admin");

        if (assignment == null)
        {
            ErrorMessage =
                "This request was already processed by another administrator.";
            return RedirectToPage();
        }

        var studentAlreadyActive = await db.SupervisorAssignments
            .AnyAsync(a =>
                a.StudentId == assignment.StudentId &&
                a.Status == "active");

        if (studentAlreadyActive)
        {
            ErrorMessage =
                "This student already has an active supervisor.";
            return RedirectToPage();
        }

        var supervisorActiveCount = await db.SupervisorAssignments
            .CountAsync(a =>
                a.SupervisorId == assignment.SupervisorId &&
                a.Status == "active");

        if (supervisorActiveCount >= SupervisorLimit)
        {
            ErrorMessage =
                "This supervisor is full. Assign a different supervisor or reject the request.";
            return RedirectToPage();
        }

        var now = DateTime.UtcNow;

        try
        {
            // Only one administrator can change this row from
            // pending_admin to active.
            var affectedRows = await db.SupervisorAssignments
                .Where(a =>
                    a.Id == assignmentId &&
                    a.Status == "pending_admin")
                .ExecuteUpdateAsync(updates => updates
                    .SetProperty(a => a.Status, "active")
                    .SetProperty(a => a.AssignedByAdminId, adminId)
                    .SetProperty(a => a.ApprovedAt, now)
                    .SetProperty(a => a.RejectedAt, (DateTime?)null)
                    .SetProperty(a => a.AdminNote, "Approved by admin.")
                    .SetProperty(a => a.UpdatedAt, now));

            if (affectedRows == 0)
            {
                ErrorMessage =
                    "This request was already processed by another administrator.";
                return RedirectToPage();
            }

            await SendAssignmentApprovedCommunicationAsync(assignmentId);

            SuccessMessage =
                "Supervisor assignment approved successfully. The student and supervisor were notified by email.";
        }
        catch (DbUpdateException)
        {
            ErrorMessage =
                "The assignment could not be approved. The student may already have an active supervisor.";
        }

        return RedirectToPage();
    }

    public async Task<IActionResult> OnPostAssignDifferentAsync(
        int assignmentId,
        int supervisorId,
        string? adminNote)
    {
        var adminId = GetCurrentUserId();

        // Read without tracking because the final update is performed
        // directly and conditionally in the database.
        var assignment = await db.SupervisorAssignments
            .AsNoTracking()
            .FirstOrDefaultAsync(a =>
                a.Id == assignmentId &&
                a.Status == "pending_admin");

        if (assignment == null)
        {
            ErrorMessage =
                "This request was already processed by another administrator.";

            return RedirectToPage();
        }

        var supervisor = await db.Users
            .Include(u => u.SupervisorProfile)
            .FirstOrDefaultAsync(u =>
                u.Id == supervisorId &&
                u.Role.ToLower() == "supervisor");

        if (supervisor == null || supervisor.SupervisorProfile == null)
        {
            ErrorMessage = "Selected supervisor was not found.";
            return RedirectToPage();
        }

        var studentAlreadyActive = await db.SupervisorAssignments
            .AnyAsync(a =>
                a.StudentId == assignment.StudentId &&
                a.Status == "active");

        if (studentAlreadyActive)
        {
            ErrorMessage =
                "This student already has an active supervisor.";

            return RedirectToPage();
        }

        var supervisorActiveCount = await db.SupervisorAssignments
            .CountAsync(a =>
                a.SupervisorId == supervisorId &&
                a.Status == "active");

        if (supervisorActiveCount >= SupervisorLimit)
        {
            ErrorMessage = "Selected supervisor is full.";
            return RedirectToPage();
        }

        var note = string.IsNullOrWhiteSpace(adminNote)
            ? "Admin assigned a different supervisor based on capacity and suitability."
            : adminNote.Trim();

        var now = DateTime.UtcNow;

        try
        {
            // Only the first administrator can change this request
            // while its status is still pending_admin.
            var affectedRows = await db.SupervisorAssignments
                .Where(a =>
                    a.Id == assignmentId &&
                    a.Status == "pending_admin")
                .ExecuteUpdateAsync(updates => updates
                    .SetProperty(a => a.SupervisorId, supervisorId)
                    .SetProperty(a => a.Status, "active")
                    .SetProperty(a => a.AssignedByAdminId, adminId)
                    .SetProperty(a => a.ApprovedAt, now)
                    .SetProperty(a => a.RejectedAt, (DateTime?)null)
                    .SetProperty(a => a.AdminNote, note)
                    .SetProperty(a => a.UpdatedAt, now));

            if (affectedRows == 0)
            {
                ErrorMessage =
                    "This request was already processed by another administrator.";

                return RedirectToPage();
            }

            await SendAssignmentApprovedCommunicationAsync(assignmentId);

            SuccessMessage =
                $"Student assigned to {supervisor.FullName} successfully. " +
                "The student and supervisor were notified by email.";
        }
        catch (DbUpdateException)
        {
            ErrorMessage =
                "The assignment could not be completed. " +
                "The student may already have an active supervisor.";
        }

        return RedirectToPage();
    }
    public async Task<IActionResult> OnPostRejectAsync(
      int assignmentId,
      string? adminNote)
    {
        var adminId = GetCurrentUserId();

        var note = string.IsNullOrWhiteSpace(adminNote)
            ? "Request was not approved by admin. Please choose another available supervisor."
            : adminNote.Trim();

        var now = DateTime.UtcNow;

        try
        {
            // Reject only when the request is still pending.
            // If another admin already approved, rejected, or reassigned it,
            // this update affects zero rows.
            var affectedRows = await db.SupervisorAssignments
                .Where(a =>
                    a.Id == assignmentId &&
                    a.Status == "pending_admin")
                .ExecuteUpdateAsync(updates => updates
                    .SetProperty(a => a.Status, "rejected")
                    .SetProperty(a => a.AssignedByAdminId, adminId)
                    .SetProperty(a => a.AdminNote, note)
                    .SetProperty(a => a.RejectedAt, now)
                    .SetProperty(a => a.ApprovedAt, (DateTime?)null)
                    .SetProperty(a => a.UpdatedAt, now));

            if (affectedRows == 0)
            {
                ErrorMessage =
                    "This request was already processed by another administrator.";

                return RedirectToPage();
            }

            await SendAssignmentRejectedCommunicationAsync(assignmentId);

            SuccessMessage =
                "Supervisor request rejected. The student was notified and can now choose another supervisor.";
        }
        catch (DbUpdateException)
        {
            ErrorMessage =
                "The supervisor request could not be rejected. Please refresh and try again.";
        }

        return RedirectToPage();
    }
    public async Task<IActionResult> OnPostTransferAsync(
        int assignmentId,
        int newSupervisorId,
        string transferReason)
    {
        var adminId = GetCurrentUserId();

        if (string.IsNullOrWhiteSpace(transferReason))
        {
            ErrorMessage = "Transfer reason is required.";
            return RedirectToPage();
        }

        // Read only. The actual status change will be performed
        // conditionally inside the database transaction.
        var currentAssignment = await db.SupervisorAssignments
            .AsNoTracking()
            .Include(a => a.Student)
            .Include(a => a.Supervisor)
            .FirstOrDefaultAsync(a =>
                a.Id == assignmentId &&
                a.Status == "active");

        if (currentAssignment == null)
        {
            ErrorMessage =
                "This assignment was already changed by another administrator.";

            return RedirectToPage();
        }

        if (currentAssignment.SupervisorId == newSupervisorId)
        {
            ErrorMessage =
                "Student is already assigned to this supervisor.";

            return RedirectToPage();
        }

        var newSupervisor = await db.Users
            .Include(u => u.SupervisorProfile)
            .FirstOrDefaultAsync(u =>
                u.Id == newSupervisorId &&
                u.Role.ToLower() == "supervisor");

        if (newSupervisor == null ||
            newSupervisor.SupervisorProfile == null)
        {
            ErrorMessage =
                "Selected new supervisor was not found.";

            return RedirectToPage();
        }

        var newSupervisorActiveCount =
            await db.SupervisorAssignments.CountAsync(a =>
                a.SupervisorId == newSupervisorId &&
                a.Status == "active");

        if (newSupervisorActiveCount >= SupervisorLimit)
        {
            ErrorMessage =
                "Selected supervisor is full. Choose another available supervisor.";

            return RedirectToPage();
        }

        var oldSupervisorId = currentAssignment.SupervisorId;

        var oldSupervisorName =
            currentAssignment.Supervisor?.FullName
            ?? "Previous supervisor";

        var studentName =
            currentAssignment.Student?.FullName
            ?? "Student";

        var reason = transferReason.Trim();
        var now = DateTime.UtcNow;

        await using var transaction =
            await db.Database.BeginTransactionAsync();

        try
        {
            // Only the first administrator can transfer this assignment
            // while it is still active.
            var affectedRows = await db.SupervisorAssignments
                .Where(a =>
                    a.Id == assignmentId &&
                    a.Status == "active")
                .ExecuteUpdateAsync(updates => updates
                    .SetProperty(a => a.Status, "transferred")
                    .SetProperty(
                        a => a.AdminNote,
                        $"Transferred to {newSupervisor.FullName}. Reason: {reason}")
                    .SetProperty(a => a.UpdatedAt, now));

            if (affectedRows == 0)
            {
                await transaction.RollbackAsync();

                ErrorMessage =
                    "This assignment was already changed by another administrator.";

                return RedirectToPage();
            }

            var newAssignment = new SupervisorAssignment
            {
                StudentId = currentAssignment.StudentId,
                SupervisorId = newSupervisorId,
                AssignedByAdminId = adminId,
                Status = "active",
                StudentMessage = currentAssignment.StudentMessage,
                AdminNote =
                    $"Transferred from {oldSupervisorName}. Reason: {reason}",
                RequestedAt = now,
                ApprovedAt = now,
                RejectedAt = null,
                UpdatedAt = now
            };

            db.SupervisorAssignments.Add(newAssignment);
            await db.SaveChangesAsync();

            await transaction.CommitAsync();

            await SendTransferCommunicationAsync(
                newAssignment.Id,
                oldSupervisorId,
                oldSupervisorName,
                reason);

            SuccessMessage =
                $"{studentName} was transferred from " +
                $"{oldSupervisorName} to {newSupervisor.FullName}. " +
                "Student and supervisors were notified.";
        }
        catch (DbUpdateException)
        {
            await transaction.RollbackAsync();

            ErrorMessage =
                "Transfer could not be completed. " +
                "The assignment may have been changed by another administrator, " +
                "or the student may already have an active supervisor.";
        }

        return RedirectToPage();
    }

    private async Task LoadAsync()
    {
        var activeLoadRows = await db.SupervisorAssignments
            .Where(a => a.Status == "active")
            .GroupBy(a => a.SupervisorId)
            .Select(g => new
            {
                SupervisorId = g.Key,
                Count = g.Count()
            })
            .ToListAsync();

        var activeLoadMap = activeLoadRows.ToDictionary(x => x.SupervisorId, x => x.Count);

        var pendingLoadRows = await db.SupervisorAssignments
            .Where(a => a.Status == "pending_admin")
            .GroupBy(a => a.SupervisorId)
            .Select(g => new
            {
                SupervisorId = g.Key,
                Count = g.Count()
            })
            .ToListAsync();

        var pendingLoadMap = pendingLoadRows.ToDictionary(x => x.SupervisorId, x => x.Count);

        var supervisors = await db.Users
            .Include(u => u.SupervisorProfile)
            .Where(u => u.Role.ToLower() == "supervisor")
            .Where(u => u.SupervisorProfile != null)
            .OrderBy(u => u.FullName)
            .ToListAsync();

        SupervisorCapacity = supervisors
            .Select(s =>
            {
                var activeCount = activeLoadMap.GetValueOrDefault(s.Id, 0);
                var pendingCount = pendingLoadMap.GetValueOrDefault(s.Id, 0);

                return new SupervisorCapacityRow
                {
                    SupervisorId = s.Id,
                    FullName = s.FullName,
                    Email = s.Email,
                    Department = s.SupervisorProfile?.Department ?? "",
                    Specialization = s.SupervisorProfile?.Specialization ?? "",
                    ResearchAreas = s.SupervisorProfile?.ResearchAreas ?? "",
                    ActiveCount = activeCount,
                    PendingCount = pendingCount,
                    CapacityLimit = SupervisorLimit,
                    IsFull = activeCount >= SupervisorLimit
                };
            })
            .OrderByDescending(s => s.IsFull)
            .ThenByDescending(s => s.ActiveCount)
            .ThenBy(s => s.FullName)
            .ToList();

        AssignableSupervisors = SupervisorCapacity
            .Where(s => !s.IsFull)
            .Select(s => new AssignableSupervisorOption
            {
                SupervisorId = s.SupervisorId,
                FullName = s.FullName,
                CapacityUsed = s.ActiveCount,
                CapacityLimit = s.CapacityLimit,
                Department = s.Department,
                Specialization = s.Specialization
            })
            .OrderBy(s => s.CapacityUsed)
            .ThenBy(s => s.FullName)
            .ToList();

        var latestIdeas = await db.ProjectIdeas
            .OrderByDescending(i => i.CreatedAt)
            .Select(i => new
            {
                i.UserId,
                i.Title,
                i.Domain,
                i.FeasibilityScore,
                i.CreatedAt
            })
            .ToListAsync();

        var latestIdeaMap = latestIdeas
            .GroupBy(i => i.UserId)
            .ToDictionary(g => g.Key, g => g.First());

        var pendingEntities = await db.SupervisorAssignments
            .Include(a => a.Student)
                .ThenInclude(s => s!.StudentProfile)
            .Include(a => a.Supervisor)
                .ThenInclude(s => s!.SupervisorProfile)
            .Where(a => a.Status == "pending_admin")
            .OrderBy(a => a.RequestedAt)
            .ToListAsync();

        PendingRequests = pendingEntities
            .Select(a =>
            {
                latestIdeaMap.TryGetValue(a.StudentId, out var idea);
                var used = activeLoadMap.GetValueOrDefault(a.SupervisorId, 0);

                return new PendingAssignmentRow
                {
                    AssignmentId = a.Id,
                    StudentId = a.StudentId,
                    StudentName = a.Student?.FullName ?? "Student",
                    StudentEmail = a.Student?.Email ?? "",
                    StudentMajor = a.Student?.StudentProfile?.Major ?? "",
                    PreferredDomain = a.Student?.StudentProfile?.PreferredDomain ?? "",
                    StudentSkills = a.Student?.StudentProfile?.Skills ?? "",
                    StudentIdeaTitle = idea?.Title ?? "No idea selected yet",
                    StudentIdeaDomain = idea?.Domain ?? "",
                    RequestedSupervisorId = a.SupervisorId,
                    RequestedSupervisorName = a.Supervisor?.FullName ?? "Supervisor",
                    RequestedSupervisorDepartment = a.Supervisor?.SupervisorProfile?.Department ?? "",
                    RequestedSupervisorSpecialization = a.Supervisor?.SupervisorProfile?.Specialization ?? "",
                    RequestedSupervisorCapacityUsed = used,
                    RequestedSupervisorCapacityLimit = SupervisorLimit,
                    RequestedSupervisorIsFull = used >= SupervisorLimit,
                    StudentMessage = a.StudentMessage,
                    RequestedAt = a.RequestedAt
                };
            })
            .ToList();

        var activeEntities = await db.SupervisorAssignments
            .Include(a => a.Student)
                .ThenInclude(s => s!.StudentProfile)
            .Include(a => a.Supervisor)
                .ThenInclude(s => s!.SupervisorProfile)
            .Where(a => a.Status == "active")
            .OrderByDescending(a => a.ApprovedAt ?? a.UpdatedAt)
            .ToListAsync();

        ActiveAssignments = activeEntities
            .Select(a =>
            {
                latestIdeaMap.TryGetValue(a.StudentId, out var idea);

                return new ActiveAssignmentRow
                {
                    AssignmentId = a.Id,
                    StudentId = a.StudentId,
                    SupervisorId = a.SupervisorId,
                    StudentName = a.Student?.FullName ?? "Student",
                    StudentEmail = a.Student?.Email ?? "",
                    StudentMajor = a.Student?.StudentProfile?.Major ?? "",
                    PreferredDomain = a.Student?.StudentProfile?.PreferredDomain ?? "",
                    SupervisorName = a.Supervisor?.FullName ?? "Supervisor",
                    SupervisorSpecialization = a.Supervisor?.SupervisorProfile?.Specialization ?? "",
                    IdeaTitle = idea?.Title ?? "No idea selected yet",
                    ApprovedAt = a.ApprovedAt ?? a.UpdatedAt
                };
            })
            .ToList();

        var rejectedEntities = await db.SupervisorAssignments
            .Include(a => a.Student)
            .Include(a => a.Supervisor)
            .Where(a => a.Status == "rejected")
            .OrderByDescending(a => a.RejectedAt ?? a.UpdatedAt)
            .Take(8)
            .ToListAsync();

        RecentRejectedRequests = rejectedEntities
            .Select(a => new RejectedAssignmentRow
            {
                AssignmentId = a.Id,
                StudentName = a.Student?.FullName ?? "Student",
                SupervisorName = a.Supervisor?.FullName ?? "Supervisor",
                AdminNote = a.AdminNote,
                RejectedAt = a.RejectedAt ?? a.UpdatedAt
            })
            .ToList();

        Stats = new AdminAssignmentStats
        {
            PendingRequests = PendingRequests.Count,
            ActiveAssignments = ActiveAssignments.Count,
            FullSupervisors = SupervisorCapacity.Count(s => s.IsFull),
            AvailableSupervisors = SupervisorCapacity.Count(s => !s.IsFull)
        };
    }

    private async Task SendTransferCommunicationAsync(
    int newAssignmentId,
    int oldSupervisorId,
    string oldSupervisorName,
    string reason)
    {
        var assignment = await db.SupervisorAssignments
            .Include(a => a.Student)
            .Include(a => a.Supervisor)
            .FirstOrDefaultAsync(a => a.Id == newAssignmentId);

        if (assignment?.Student == null || assignment.Supervisor == null)
        {
            return;
        }

        var oldSupervisor = await db.Users
            .FirstOrDefaultAsync(u => u.Id == oldSupervisorId);

        var latestIdea = await db.ProjectIdeas
            .Where(i => i.UserId == assignment.StudentId)
            .OrderByDescending(i => i.CreatedAt)
            .FirstOrDefaultAsync();

        var studentName = assignment.Student.FullName;
        var newSupervisorName = assignment.Supervisor.FullName;
        var ideaTitle = latestIdea?.Title ?? "your selected project idea";

        await notificationService.NotifyUserAsync(
            assignment.StudentId,
            "Supervisor Assignment Updated",
            $"Your supervisor has been changed from {oldSupervisorName} to {newSupervisorName}. Reason: {reason}",
            "assignment_transferred",
            "/Student/Feedback",
            sendEmail: true,
            emailSubject: "Your FYP supervisor has been changed",
            emailHtmlBody: BuildTransferStudentEmail(
                studentName,
                oldSupervisorName,
                newSupervisorName,
                ideaTitle,
                reason));

        if (oldSupervisor != null)
        {
            await notificationService.NotifyUserAsync(
                oldSupervisorId,
                "Student Transferred",
                $"{studentName} has been transferred from you to {newSupervisorName}. Reason: {reason}",
                "student_transferred_out",
                "/Supervisor/IdeaReview",
                sendEmail: true,
                emailSubject: "A student has been transferred from your supervision",
                emailHtmlBody: BuildTransferOldSupervisorEmail(
                    oldSupervisor.FullName,
                    studentName,
                    newSupervisorName,
                    reason));
        }

        await notificationService.NotifyUserAsync(
            assignment.SupervisorId,
            "New Student Transferred To You",
            $"{studentName} has been transferred to your supervision. You can now review ideas and schedule meetings.",
            "student_transferred_in",
            "/Supervisor/IdeaReview",
            sendEmail: true,
            emailSubject: "A student has been transferred to your supervision",
            emailHtmlBody: BuildTransferNewSupervisorEmail(
                newSupervisorName,
                studentName,
                oldSupervisorName,
                ideaTitle,
                reason));
    }
    private async Task SendAssignmentApprovedCommunicationAsync(int assignmentId)
    {
        var assignment = await db.SupervisorAssignments
            .Include(a => a.Student)
            .Include(a => a.Supervisor)
            .FirstOrDefaultAsync(a => a.Id == assignmentId);

        if (assignment?.Student == null || assignment.Supervisor == null)
        {
            return;
        }

        var latestIdea = await db.ProjectIdeas
            .Where(i => i.UserId == assignment.StudentId)
            .OrderByDescending(i => i.CreatedAt)
            .FirstOrDefaultAsync();

        var supervisorName = assignment.Supervisor.FullName;
        var studentName = assignment.Student.FullName;
        var ideaTitle = latestIdea?.Title ?? "your selected project idea";

        await notificationService.NotifyUserAsync(
            assignment.StudentId,
            "Supervisor Assignment Approved",
            $"You have been assigned to {supervisorName}. {supervisorName} will follow up with you on {ideaTitle}.",
            "assignment_approved",
            "/Student/Feedback",
            sendEmail: true,
            emailSubject: "Your FYP supervisor has been assigned",
            emailHtmlBody: BuildAssignmentStudentEmail(studentName, supervisorName, ideaTitle));

        await notificationService.NotifyUserAsync(
            assignment.SupervisorId,
            "New Student Assigned",
            $"{studentName} has been assigned to you. You can now review the student's ideas, schedule meetings, and follow progress.",
            "student_assigned",
            "/Supervisor/IdeaReview",
            sendEmail: true,
            emailSubject: "A new student has been assigned to you",
            emailHtmlBody: BuildAssignmentSupervisorEmail(supervisorName, studentName, ideaTitle));
    }

    private async Task SendAssignmentRejectedCommunicationAsync(int assignmentId)
    {
        var assignment = await db.SupervisorAssignments
            .Include(a => a.Student)
            .Include(a => a.Supervisor)
            .FirstOrDefaultAsync(a => a.Id == assignmentId);

        if (assignment?.Student == null)
        {
            return;
        }

        var supervisorName = assignment.Supervisor?.FullName ?? "the selected supervisor";

        var title = "Supervisor Request Not Approved";
        var message =
            $"Your request for {supervisorName} was not approved by admin. Please choose another available supervisor.";

        if (!string.IsNullOrWhiteSpace(assignment.AdminNote))
        {
            message += $" Admin note: {assignment.AdminNote}";
        }

        await notificationService.NotifyUserAsync(
            assignment.StudentId,
            title,
            message,
            "assignment_rejected",
            "/Student/Feedback",
            sendEmail: true,
            emailSubject: "Your supervisor request was not approved",
            emailHtmlBody: BuildRejectedStudentEmail(
                assignment.Student.FullName,
                supervisorName,
                assignment.AdminNote));
    }

    private static string BuildAssignmentStudentEmail(string studentName, string supervisorName, string ideaTitle)
    {
        studentName = System.Net.WebUtility.HtmlEncode(studentName);
        supervisorName = System.Net.WebUtility.HtmlEncode(supervisorName);
        ideaTitle = System.Net.WebUtility.HtmlEncode(ideaTitle);

        return $"""
        <div style="font-family:Arial,sans-serif;background:#F7F8FA;padding:28px;">
            <div style="max-width:640px;margin:auto;background:#ffffff;border:1px solid #E8ECF2;border-radius:18px;padding:26px;">
                <h2 style="color:#28385E;margin-top:0;">Supervisor Assignment Approved</h2>
                <p style="color:#475569;line-height:1.7;">
                    Hello {studentName},
                </p>
                <p style="color:#475569;line-height:1.7;">
                    Your FYP supervisor assignment has been approved.
                    <strong>{supervisorName}</strong> will follow up with you on:
                </p>
                <div style="background:#F7F8FA;border:1px solid #E8ECF2;border-radius:14px;padding:14px;color:#28385E;font-weight:700;">
                    {ideaTitle}
                </div>
                <p style="color:#475569;line-height:1.7;margin-top:18px;">
                    You can now open Supervisor Feedback, communicate with your supervisor, and receive meeting updates.
                </p>
                <p style="color:#94A3B8;font-size:12px;margin-top:24px;">FYPilot automated notification</p>
            </div>
        </div>
        """;
    }

    private static string BuildAssignmentSupervisorEmail(string supervisorName, string studentName, string ideaTitle)
    {
        supervisorName = System.Net.WebUtility.HtmlEncode(supervisorName);
        studentName = System.Net.WebUtility.HtmlEncode(studentName);
        ideaTitle = System.Net.WebUtility.HtmlEncode(ideaTitle);

        return $"""
        <div style="font-family:Arial,sans-serif;background:#F7F8FA;padding:28px;">
            <div style="max-width:640px;margin:auto;background:#ffffff;border:1px solid #E8ECF2;border-radius:18px;padding:26px;">
                <h2 style="color:#28385E;margin-top:0;">New Student Assigned</h2>
                <p style="color:#475569;line-height:1.7;">
                    Hello {supervisorName},
                </p>
                <p style="color:#475569;line-height:1.7;">
                    <strong>{studentName}</strong> has been officially assigned to you.
                </p>
                <div style="background:#F7F8FA;border:1px solid #E8ECF2;border-radius:14px;padding:14px;color:#28385E;font-weight:700;">
                    Project idea: {ideaTitle}
                </div>
                <p style="color:#475569;line-height:1.7;margin-top:18px;">
                    You can now review the student's ideas, schedule meetings, and follow progress through FYPilot.
                </p>
                <p style="color:#94A3B8;font-size:12px;margin-top:24px;">FYPilot automated notification</p>
            </div>
        </div>
        """;
    }

    private static string BuildRejectedStudentEmail(string studentName, string supervisorName, string adminNote)
    {
        studentName = System.Net.WebUtility.HtmlEncode(studentName);
        supervisorName = System.Net.WebUtility.HtmlEncode(supervisorName);
        adminNote = System.Net.WebUtility.HtmlEncode(adminNote);

        var noteHtml = string.IsNullOrWhiteSpace(adminNote)
            ? ""
            : $"""
              <div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:14px;padding:14px;color:#991B1B;margin-top:14px;">
                  <strong>Admin note:</strong> {adminNote}
              </div>
              """;

        return $"""
        <div style="font-family:Arial,sans-serif;background:#F7F8FA;padding:28px;">
            <div style="max-width:640px;margin:auto;background:#ffffff;border:1px solid #E8ECF2;border-radius:18px;padding:26px;">
                <h2 style="color:#28385E;margin-top:0;">Supervisor Request Not Approved</h2>
                <p style="color:#475569;line-height:1.7;">
                    Hello {studentName},
                </p>
                <p style="color:#475569;line-height:1.7;">
                    Your request for <strong>{supervisorName}</strong> was not approved by admin.
                    Please choose another available supervisor from the Supervisor Feedback page.
                </p>
                {noteHtml}
                <p style="color:#94A3B8;font-size:12px;margin-top:24px;">FYPilot automated notification</p>
            </div>
        </div>
        """;
    }
    private static string BuildTransferStudentEmail(
    string studentName,
    string oldSupervisorName,
    string newSupervisorName,
    string ideaTitle,
    string reason)
    {
        studentName = System.Net.WebUtility.HtmlEncode(studentName);
        oldSupervisorName = System.Net.WebUtility.HtmlEncode(oldSupervisorName);
        newSupervisorName = System.Net.WebUtility.HtmlEncode(newSupervisorName);
        ideaTitle = System.Net.WebUtility.HtmlEncode(ideaTitle);
        reason = System.Net.WebUtility.HtmlEncode(reason);

        return $"""
    <div style="font-family:Arial,sans-serif;background:#F7F8FA;padding:28px;">
        <div style="max-width:640px;margin:auto;background:#ffffff;border:1px solid #E8ECF2;border-radius:18px;padding:26px;">
            <h2 style="color:#28385E;margin-top:0;">Supervisor Assignment Updated</h2>

            <p style="color:#475569;line-height:1.7;">
                Hello {studentName},
            </p>

            <p style="color:#475569;line-height:1.7;">
                Your FYP supervisor has been changed from
                <strong>{oldSupervisorName}</strong> to
                <strong>{newSupervisorName}</strong>.
            </p>

            <div style="background:#F7F8FA;border:1px solid #E8ECF2;border-radius:14px;padding:14px;color:#28385E;margin-top:14px;">
                <strong>Project idea:</strong> {ideaTitle}<br/>
                <strong>Reason:</strong> {reason}
            </div>

            <p style="color:#475569;line-height:1.7;margin-top:18px;">
                You can continue your FYP follow-up with your new supervisor through FYPilot.
            </p>

            <p style="color:#94A3B8;font-size:12px;margin-top:24px;">FYPilot automated notification</p>
        </div>
    </div>
    """;
    }

    private static string BuildTransferOldSupervisorEmail(
        string oldSupervisorName,
        string studentName,
        string newSupervisorName,
        string reason)
    {
        oldSupervisorName = System.Net.WebUtility.HtmlEncode(oldSupervisorName);
        studentName = System.Net.WebUtility.HtmlEncode(studentName);
        newSupervisorName = System.Net.WebUtility.HtmlEncode(newSupervisorName);
        reason = System.Net.WebUtility.HtmlEncode(reason);

        return $"""
    <div style="font-family:Arial,sans-serif;background:#F7F8FA;padding:28px;">
        <div style="max-width:640px;margin:auto;background:#ffffff;border:1px solid #E8ECF2;border-radius:18px;padding:26px;">
            <h2 style="color:#28385E;margin-top:0;">Student Transferred</h2>

            <p style="color:#475569;line-height:1.7;">
                Hello {oldSupervisorName},
            </p>

            <p style="color:#475569;line-height:1.7;">
                <strong>{studentName}</strong> has been transferred from your supervision to
                <strong>{newSupervisorName}</strong>.
            </p>

            <div style="background:#F7F8FA;border:1px solid #E8ECF2;border-radius:14px;padding:14px;color:#28385E;margin-top:14px;">
                <strong>Reason:</strong> {reason}
            </div>

            <p style="color:#94A3B8;font-size:12px;margin-top:24px;">FYPilot automated notification</p>
        </div>
    </div>
    """;
    }

    private static string BuildTransferNewSupervisorEmail(
        string newSupervisorName,
        string studentName,
        string oldSupervisorName,
        string ideaTitle,
        string reason)
    {
        newSupervisorName = System.Net.WebUtility.HtmlEncode(newSupervisorName);
        studentName = System.Net.WebUtility.HtmlEncode(studentName);
        oldSupervisorName = System.Net.WebUtility.HtmlEncode(oldSupervisorName);
        ideaTitle = System.Net.WebUtility.HtmlEncode(ideaTitle);
        reason = System.Net.WebUtility.HtmlEncode(reason);

        return $"""
    <div style="font-family:Arial,sans-serif;background:#F7F8FA;padding:28px;">
        <div style="max-width:640px;margin:auto;background:#ffffff;border:1px solid #E8ECF2;border-radius:18px;padding:26px;">
            <h2 style="color:#28385E;margin-top:0;">New Student Transferred To You</h2>

            <p style="color:#475569;line-height:1.7;">
                Hello {newSupervisorName},
            </p>

            <p style="color:#475569;line-height:1.7;">
                <strong>{studentName}</strong> has been transferred to your supervision.
            </p>

            <div style="background:#F7F8FA;border:1px solid #E8ECF2;border-radius:14px;padding:14px;color:#28385E;margin-top:14px;">
                <strong>Previous supervisor:</strong> {oldSupervisorName}<br/>
                <strong>Project idea:</strong> {ideaTitle}<br/>
                <strong>Reason:</strong> {reason}
            </div>

            <p style="color:#475569;line-height:1.7;margin-top:18px;">
                You can now review the student's ideas, schedule meetings, and follow progress through FYPilot.
            </p>

            <p style="color:#94A3B8;font-size:12px;margin-top:24px;">FYPilot automated notification</p>
        </div>
    </div>
    """;
    }
    private int GetCurrentUserId()
    {
        var userIdClaim = User.FindFirst(ClaimTypes.NameIdentifier)?.Value;

        if (int.TryParse(userIdClaim, out var userId))
        {
            return userId;
        }

        throw new InvalidOperationException("Unable to identify the current logged-in user.");
    }

    public class AdminAssignmentStats
    {
        public int PendingRequests { get; set; }
        public int ActiveAssignments { get; set; }
        public int FullSupervisors { get; set; }
        public int AvailableSupervisors { get; set; }
    }

    public class PendingAssignmentRow
    {
        public int AssignmentId { get; set; }
        public int StudentId { get; set; }
        public string StudentName { get; set; } = "";
        public string StudentEmail { get; set; } = "";
        public string StudentMajor { get; set; } = "";
        public string PreferredDomain { get; set; } = "";
        public string StudentSkills { get; set; } = "";
        public string StudentIdeaTitle { get; set; } = "";
        public string StudentIdeaDomain { get; set; } = "";
        public int RequestedSupervisorId { get; set; }
        public string RequestedSupervisorName { get; set; } = "";
        public string RequestedSupervisorDepartment { get; set; } = "";
        public string RequestedSupervisorSpecialization { get; set; } = "";
        public int RequestedSupervisorCapacityUsed { get; set; }
        public int RequestedSupervisorCapacityLimit { get; set; }
        public bool RequestedSupervisorIsFull { get; set; }
        public string StudentMessage { get; set; } = "";
        public DateTime RequestedAt { get; set; }
    }

    public class SupervisorCapacityRow
    {
        public int SupervisorId { get; set; }
        public string FullName { get; set; } = "";
        public string Email { get; set; } = "";
        public string Department { get; set; } = "";
        public string Specialization { get; set; } = "";
        public string ResearchAreas { get; set; } = "";
        public int ActiveCount { get; set; }
        public int PendingCount { get; set; }
        public int CapacityLimit { get; set; }
        public bool IsFull { get; set; }
    }

    public class AssignableSupervisorOption
    {
        public int SupervisorId { get; set; }
        public string FullName { get; set; } = "";
        public string Department { get; set; } = "";
        public string Specialization { get; set; } = "";
        public int CapacityUsed { get; set; }
        public int CapacityLimit { get; set; }
    }

    public class ActiveAssignmentRow
    {
        public int AssignmentId { get; set; }
        public int StudentId { get; set; }
        public int SupervisorId { get; set; }
        public string StudentName { get; set; } = "";
        public string StudentEmail { get; set; } = "";
        public string StudentMajor { get; set; } = "";
        public string PreferredDomain { get; set; } = "";
        public string SupervisorName { get; set; } = "";
        public string SupervisorSpecialization { get; set; } = "";
        public string IdeaTitle { get; set; } = "";
        public DateTime ApprovedAt { get; set; }
    }

    public class RejectedAssignmentRow
    {
        public int AssignmentId { get; set; }
        public string StudentName { get; set; } = "";
        public string SupervisorName { get; set; } = "";
        public string AdminNote { get; set; } = "";
        public DateTime RejectedAt { get; set; }
    }
}
