using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class FeedbackModel(ApplicationDbContext db) : PageModel
{
    private const int SupervisorLimit = 4;

    public List<FeedbackItem> Feedbacks { get; private set; } = [];

    public FeedbackItem? SelectedFeedback { get; private set; }

    public int CurrentUserId { get; private set; }

    public SupervisorAssignment? CurrentAssignment { get; private set; }

    public List<SupervisorOption> AvailableSupervisors { get; private set; } = [];

    public bool CanAccessFeedback =>
        CurrentAssignment != null && CurrentAssignment.Status == "active";

    [BindProperty]
    public int SelectedEvaluationId { get; set; }

    [BindProperty]
    public string ReplyText { get; set; } = "";

    [TempData]
    public string? SuccessMessage { get; set; }

    [TempData]
    public string? ErrorMessage { get; set; }

    public record FeedbackItem(
        ProjectIdea Idea,
        SupervisorEvaluation Evaluation,
        string SupervisorName,
        List<FeedbackMessage> Messages);

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

        public int CapacityLimit { get; set; } = SupervisorLimit;

        public int MatchScore { get; set; }

        public string MatchReason { get; set; } = "";
    }

    public async Task OnGetAsync(int? evaluationId)
    {
        await LoadSupervisorGateAsync();

        if (!CanAccessFeedback)
        {
            Feedbacks = [];
            SelectedFeedback = null;
            return;
        }

        await LoadFeedbackAsync(evaluationId);
    }

    public async Task<IActionResult> OnPostRequestSupervisorAsync(int supervisorId, string? studentMessage)
    {
        CurrentUserId = GetCurrentUserId();

        var existingActiveOrPending = await db.SupervisorAssignments
            .AnyAsync(a =>
                a.StudentId == CurrentUserId &&
                (a.Status == "pending_admin" || a.Status == "active"));

        if (existingActiveOrPending)
        {
            ErrorMessage = "You already have a pending or active supervisor request.";
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

        var activeCount = await db.SupervisorAssignments
            .CountAsync(a =>
                a.SupervisorId == supervisorId &&
                a.Status == "active");

        if (activeCount >= SupervisorLimit)
        {
            ErrorMessage = "This supervisor is already full. Please choose another supervisor.";
            return RedirectToPage();
        }

        db.SupervisorAssignments.Add(new SupervisorAssignment
        {
            StudentId = CurrentUserId,
            SupervisorId = supervisorId,
            Status = "pending_admin",
            StudentMessage = studentMessage?.Trim() ?? "",
            RequestedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow
        });

        await db.SaveChangesAsync();

        SuccessMessage = "Your supervisor request was submitted. Please wait for admin approval.";
        return RedirectToPage();
    }

    public async Task<IActionResult> OnPostReplyAsync()
    {
        CurrentUserId = GetCurrentUserId();

        await LoadSupervisorGateAsync();

        if (!CanAccessFeedback || CurrentAssignment == null)
        {
            ErrorMessage = "You can reply to supervisor feedback after your supervisor assignment is approved by admin.";
            return RedirectToPage();
        }

        if (SelectedEvaluationId <= 0)
        {
            ErrorMessage = "Please select a feedback item before sending a reply.";
            return RedirectToPage();
        }

        if (string.IsNullOrWhiteSpace(ReplyText))
        {
            ErrorMessage = "Please write a reply before sending.";
            return RedirectToPage(new { evaluationId = SelectedEvaluationId });
        }

        var evaluation = await db.SupervisorEvaluations
            .Include(e => e.Idea)
            .FirstOrDefaultAsync(e =>
                e.Id == SelectedEvaluationId &&
                e.SupervisorId == CurrentAssignment.SupervisorId &&
                e.Idea != null &&
                e.Idea.UserId == CurrentUserId);

        if (evaluation == null)
        {
            ErrorMessage = "This feedback was not found or does not belong to your approved supervisor.";
            return RedirectToPage();
        }

        db.FeedbackMessages.Add(new FeedbackMessage
        {
            EvaluationId = SelectedEvaluationId,
            SenderUserId = CurrentUserId,
            MessageText = ReplyText.Trim(),
            CreatedAt = DateTime.UtcNow
        });

        await db.SaveChangesAsync();

        SuccessMessage = "Reply sent successfully.";

        return RedirectToPage(new { evaluationId = SelectedEvaluationId });
    }

    private async Task LoadSupervisorGateAsync()
    {
        CurrentUserId = GetCurrentUserId();

        CurrentAssignment = await db.SupervisorAssignments
            .Include(a => a.Supervisor)
                .ThenInclude(s => s!.SupervisorProfile)
            .Where(a => a.StudentId == CurrentUserId)
            .OrderByDescending(a => a.UpdatedAt)
            .ThenByDescending(a => a.RequestedAt)
            .FirstOrDefaultAsync();

        if (CurrentAssignment != null &&
            (CurrentAssignment.Status == "pending_admin" || CurrentAssignment.Status == "active"))
        {
            AvailableSupervisors = [];
            return;
        }

        await LoadAvailableSupervisorsAsync();
    }

    private async Task LoadAvailableSupervisorsAsync()
    {
        var studentProfile = await db.StudentProfiles
            .FirstOrDefaultAsync(p => p.UserId == CurrentUserId);

        var activeLoads = await db.SupervisorAssignments
            .Where(a => a.Status == "active")
            .GroupBy(a => a.SupervisorId)
            .Select(g => new
            {
                SupervisorId = g.Key,
                Count = g.Count()
            })
            .ToListAsync();

        var loadMap = activeLoads.ToDictionary(x => x.SupervisorId, x => x.Count);

        var supervisors = await db.Users
            .Include(u => u.SupervisorProfile)
            .Where(u => u.Role.ToLower() == "supervisor")
            .Where(u => u.SupervisorProfile != null)
            .ToListAsync();

        AvailableSupervisors = supervisors
            .Where(s => loadMap.GetValueOrDefault(s.Id, 0) < SupervisorLimit)
            .Select(s =>
            {
                var profile = s.SupervisorProfile!;
                var match = CalculateMatch(studentProfile, profile);

                return new SupervisorOption
                {
                    SupervisorId = s.Id,
                    FullName = s.FullName,
                    AcademicTitle = profile.AcademicTitle ?? "",
                    Department = profile.Department ?? "",
                    Faculty = profile.Faculty ?? "",
                    Specialization = profile.Specialization ?? "",
                    ResearchAreas = profile.ResearchAreas ?? "",
                    OfficeHours = profile.OfficeHours ?? "",
                    MeetingMode = profile.PreferredMeetingMode ?? "",
                    CapacityUsed = loadMap.GetValueOrDefault(s.Id, 0),
                    CapacityLimit = SupervisorLimit,
                    MatchScore = match.Score,
                    MatchReason = match.Reason
                };
            })
            .Where(s => s.MatchScore > 0)
            .OrderByDescending(s => s.MatchScore)
            .ThenBy(s => s.CapacityUsed)
            .ThenBy(s => s.FullName)
            .ToList();
    }

    private async Task LoadFeedbackAsync(int? evaluationId)
    {
        CurrentUserId = GetCurrentUserId();

        if (CurrentAssignment == null || CurrentAssignment.Status != "active")
        {
            Feedbacks = [];
            SelectedFeedback = null;
            return;
        }

        var ideaIds = await db.ProjectIdeas
            .Where(i => i.UserId == CurrentUserId)
            .Select(i => i.Id)
            .ToListAsync();

        if (!ideaIds.Any())
        {
            Feedbacks = [];
            SelectedFeedback = null;
            return;
        }

        var evals = await db.SupervisorEvaluations
            .Where(e =>
                ideaIds.Contains(e.IdeaId) &&
                e.SupervisorId == CurrentAssignment.SupervisorId)
            .Include(e => e.Idea)
            .Include(e => e.Supervisor)
            .OrderByDescending(e => e.UpdatedAt)
            .ToListAsync();

        var evaluationIds = evals
            .Select(e => e.Id)
            .ToList();

        var messages = await db.FeedbackMessages
            .Where(m => evaluationIds.Contains(m.EvaluationId))
            .OrderBy(m => m.CreatedAt)
            .ToListAsync();

        Feedbacks = evals
            .Where(e => e.Idea != null)
            .Select(e => new FeedbackItem(
                e.Idea!,
                e,
                e.Supervisor?.FullName ?? "Supervisor",
                messages.Where(m => m.EvaluationId == e.Id).ToList()
            ))
            .ToList();

        if (!Feedbacks.Any())
        {
            SelectedFeedback = null;
            return;
        }

        SelectedFeedback = evaluationId.HasValue
            ? Feedbacks.FirstOrDefault(f => f.Evaluation.Id == evaluationId.Value)
            : Feedbacks.FirstOrDefault();

        SelectedFeedback ??= Feedbacks.FirstOrDefault();

        SelectedEvaluationId = SelectedFeedback?.Evaluation.Id ?? 0;
    }

    private static (int Score, string Reason) CalculateMatch(StudentProfile? studentProfile, SupervisorProfile supervisorProfile)
    {
        if (studentProfile == null)
        {
            return (10, "General supervisor profile match");
        }

        var score = 0;
        var reasons = new List<string>();

        var major = studentProfile.Major ?? "";
        var domain = studentProfile.PreferredDomain ?? "";
        var interests = studentProfile.Interests ?? "";
        var skills = studentProfile.Skills ?? "";
        var preferredStack = studentProfile.PreferredStack ?? "";

        var department = supervisorProfile.Department ?? "";
        var faculty = supervisorProfile.Faculty ?? "";
        var specialization = supervisorProfile.Specialization ?? "";
        var researchAreas = supervisorProfile.ResearchAreas ?? "";

        if (ContainsAny(department, major) ||
            ContainsAny(faculty, major) ||
            ContainsAny(specialization, major) ||
            ContainsAny(researchAreas, major))
        {
            score += 40;
            reasons.Add("matches your major");
        }

        if (ContainsAny(specialization, domain) ||
            ContainsAny(researchAreas, domain))
        {
            score += 30;
            reasons.Add("matches your preferred domain");
        }

        if (HasAnySharedToken(interests, researchAreas) ||
            HasAnySharedToken(interests, specialization))
        {
            score += 15;
            reasons.Add("matches your interests");
        }

        if (HasAnySharedToken(skills, researchAreas) ||
            HasAnySharedToken(preferredStack, researchAreas) ||
            HasAnySharedToken(skills, specialization) ||
            HasAnySharedToken(preferredStack, specialization))
        {
            score += 15;
            reasons.Add("matches your skills or stack");
        }

        score = Math.Clamp(score, 0, 100);

        if (score == 0)
        {
            return (0, "");
        }

        return (score, string.Join(", ", reasons.Distinct()));
    }

    private static bool ContainsAny(string source, string value)
    {
        if (string.IsNullOrWhiteSpace(source) || string.IsNullOrWhiteSpace(value))
        {
            return false;
        }

        return source.Contains(value.Trim(), StringComparison.OrdinalIgnoreCase) ||
               value.Contains(source.Trim(), StringComparison.OrdinalIgnoreCase);
    }

    private static bool HasAnySharedToken(string left, string right)
    {
        if (string.IsNullOrWhiteSpace(left) || string.IsNullOrWhiteSpace(right))
        {
            return false;
        }

        var leftTokens = left
            .Split(',', ';', '/', '|')
            .Select(x => x.Trim())
            .Where(x => x.Length >= 2)
            .ToList();

        return leftTokens.Any(token =>
            right.Contains(token, StringComparison.OrdinalIgnoreCase));
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
}