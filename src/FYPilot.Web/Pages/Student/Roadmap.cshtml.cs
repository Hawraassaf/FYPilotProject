using System.Security.Claims;
using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

/// <summary>
/// Display-only view of one task inside a persisted RoadmapPhase.TasksJson
/// blob. Parsed defensively: TasksJson may hold either the new structured
/// task shape (RoadmapTaskDto[]) or, for roadmaps saved before this
/// improvement, a plain string[] -- ParseTasks below handles both so old
/// saved roadmaps keep rendering instead of erroring.
/// </summary>
public sealed record RoadmapTaskView(
    string Title,
    int? EstimatedHours,
    int? EstimatedWorkingDays,
    List<string> AssignedMembers,
    List<string> Dependencies,
    string? Complexity,
    string? Priority,
    List<RoadmapMemberAllocationDto> MemberAllocations
);

public sealed record RoadmapMemberWorkloadView(
    string Member,
    int AssignedTaskCount,
    int AssignedHours,
    double UtilizationPercentage
);

/// <summary>
/// Optional/medium-priority task deferred by the overload-resolution pass
/// at generation time, persisted inside a synthetic RoadmapPhase (see
/// RoadmapModel.DeferredScopePhaseMarker) so no migration is needed and no
/// task is ever silently deleted.
/// </summary>
public sealed record RoadmapDeferredTaskView(
    string Title,
    string Description,
    int EstimatedHours,
    string ReasonDeferred,
    string OriginalPhase,
    string Priority
);

public sealed record RoadmapWorkloadSummaryView(
    int TotalWeeks,
    int TeamSize,
    int HoursPerWeekPerMember,
    int TotalCapacityHours,
    int TotalPlannedHours,
    double UtilizationPercentage,
    List<RoadmapMemberWorkloadView> WorkloadByMember,
    List<string> Warnings,
    string ScheduleFeasibility,
    int OriginalPlannedHours,
    int AdjustedPlannedHours,
    int DeferredHours,
    int OverloadHours,
    int RecommendedAdditionalWeeks
);

[Authorize(Roles = "student")]
public class RoadmapModel(ApplicationDbContext db, IAiServiceClient aiService) : PageModel
{
    /// <summary>
    /// Marks the one synthetic RoadmapPhase row (if any) that holds
    /// deferred/future-enhancement tasks instead of a real project phase --
    /// reuses the existing Name/TasksJson/EstimatedWeeks(=0) columns so no
    /// migration is needed. Never shown as a normal phase card; excluded
    /// from Phases and surfaced instead via DeferredTasks.
    /// </summary>
    public const string DeferredScopePhaseMarker = "__DEFERRED_SCOPE__";

    public ProjectIdea? Idea { get; private set; }
    public List<RoadmapPhase> Phases { get; private set; } = [];

    /// <summary>
    /// Tasks the overload-resolution pass deferred to keep the plan within
    /// team capacity -- never silently deleted, parsed from the synthetic
    /// deferred-scope phase's TasksJson.
    /// </summary>
    public List<RoadmapDeferredTaskView> DeferredTasks { get; private set; } = [];

    /// <summary>
    /// Recomputed on every page load from the persisted per-task JSON
    /// already stored in each phase's TasksJson (hours + assignedMembers),
    /// combined with the student's current team size/available hours --
    /// deliberately NOT persisted as its own column, so no migration is
    /// needed and the figures always reflect the student's latest profile.
    /// </summary>
    public RoadmapWorkloadSummaryView? WorkloadSummary { get; private set; }

    public string? ErrorMessage { get; private set; }
    public bool LlmUsed { get; private set; }
    public string? Source { get; private set; }

    /// <summary>
    /// The AI Quality Passport for the most recently generated roadmap for
    /// this idea, loaded from the database so it survives the
    /// Post/Redirect/Get cycle. Reuses the same AiOutputReview entity and
    /// AiQualityPassportDto as Mentor Chat -- linked via ProjectIdeaId
    /// rather than a chat session, no new column or migration needed.
    /// </summary>
    public AiOutputReview? LatestReview { get; private set; }

    public (string CssClass, string Label) DescribeReview(AiOutputReview review) => review.Status switch
    {
        "approved" => ("bg-success", "Reviewed"),
        "approved_with_minor_warnings" => ("bg-success", "Reviewed · minor notes"),
        "unresolved" => ("bg-warning text-dark", "Unresolved · shown as-is"),
        "rejected" => ("bg-danger", "Rejected · showing safe roadmap"),
        "firewall_blocked" => ("bg-danger", "Blocked by content firewall"),
        "review_unavailable" => ("bg-secondary", "Not semantically reviewed"),
        "provider_unavailable" => ("bg-secondary", "AI service unavailable"),
        "schema_invalid" => ("bg-secondary", "Formatting issue"),
        _ => ("bg-secondary", review.Status),
    };

    public async Task OnGetAsync(int? ideaId)
    {
        var userId = UserId();
        await LoadPageDataAsync(userId, ideaId);
    }

    public async Task<IActionResult> OnPostGenerateAsync(int ideaId)
    {
        var userId = UserId();

        await LoadPageDataAsync(userId, ideaId);

        if (Idea == null)
        {
            TempData["Error"] = "Project idea was not found.";
            return RedirectToPage();
        }

        var studentSkills = await db.StudentSkills
            .Where(s => s.UserId == userId)
            .ToListAsync();

        var profile = await db.StudentProfiles
            .FirstOrDefaultAsync(p => p.UserId == userId);

        var request = BuildRoadmapRequest(Idea, profile, studentSkills);

        var response = await aiService.GenerateProjectRoadmapAsync(request);

        if (response?.Roadmap == null)
        {
            ErrorMessage = "AI roadmap could not be generated. Make sure the Python AI service is running.";
            return Page();
        }

        LlmUsed = response.LlmUsed;
        Source = response.Source;

        var existingRoadmaps = await db.ProjectRoadmaps
            .Include(r => r.Phases)
            .Where(r => r.IdeaId == ideaId && r.UserId == userId)
            .ToListAsync();

        db.ProjectRoadmaps.RemoveRange(existingRoadmaps);

        var phases = ConvertAiRoadmapToPhases(
            response.Roadmap,
            Idea.RequiredTechnologies
        );

        var roadmap = new ProjectRoadmap
        {
            IdeaId = ideaId,
            UserId = userId,
            Phases = phases
        };

        db.ProjectRoadmaps.Add(roadmap);

        var review = response.Review;

        if (review != null)
        {
            db.AiOutputReviews.Add(new AiOutputReview
            {
                ReviewRunId = Guid.TryParse(review.ReviewRunId, out var reviewRunId)
                    ? reviewRunId
                    : Guid.NewGuid(),
                UserId = userId,
                ProjectIdeaId = ideaId,
                MentorChatSessionId = null,
                AgentName = "ProjectRoadmapAgent",
                Status = review.Status,
                Usable = review.Usable,
                WasRewritten = review.Attempts > 1,
                Attempts = review.Attempts,
                QualityScore = review.QualityScore,
                DecisionReason = review.DecisionReason,
                GeneratorProvider = response.Provider,
                GeneratorModel = response.ModelUsed,
                ReviewerProvider = review.ReviewerProvider,
                ReviewerModel = review.ReviewerModel,
                FirewallStatus = review.Status == "firewall_blocked" ? "blocked" : "passed",
                FirewallInputFlagsJson = JsonSerializer.Serialize(review.FirewallInputFlags ?? []),
                FirewallOutputFlagsJson = JsonSerializer.Serialize(review.FirewallOutputFlags ?? []),
                IssuesJson = JsonSerializer.Serialize(review.Issues),
                StrengthsJson = JsonSerializer.Serialize(review.Strengths),
                AttemptHistoryJson = JsonSerializer.Serialize(review.AttemptHistory ?? []),
                ReviewerVersion = review.ReviewerVersion,
                CreatedAt = DateTime.UtcNow,
                CompletedAt = DateTime.UtcNow
            });
        }

        await db.SaveChangesAsync();

        var realPhaseCount = phases.Count(p => p.Name != DeferredScopePhaseMarker);
        TempData["Success"] = $"AI roadmap with {realPhaseCount} phases generated.";
        return RedirectToPage(new { ideaId });
    }

    public async Task<IActionResult> OnPostCompleteAsync(
       int phaseId,
       int ideaId)
    {
        var userId = UserId();

        var updatedRows = await db.RoadmapPhases
            .Where(p =>
                p.Id == phaseId &&
                p.Roadmap != null &&
                p.Roadmap.UserId == userId &&
                p.Roadmap.IdeaId == ideaId)
            .ExecuteUpdateAsync(updates => updates
                .SetProperty(
                    p => p.IsCompleted,
                    true));

        if (updatedRows == 0)
        {
            TempData["Error"] =
                "This roadmap phase was not found or does not belong to your account.";

            return RedirectToPage(new
            {
                ideaId
            });
        }

        TempData["Success"] =
            "Phase marked as completed.";

        return RedirectToPage(new
        {
            ideaId
        });
    }

    private async Task LoadPageDataAsync(int userId, int? ideaId)
    {
        Idea = ideaId.HasValue
            ? await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId && i.UserId == userId)
            : await db.ProjectIdeas.FirstOrDefaultAsync(i => i.UserId == userId && i.IsSelected)
              ?? await db.ProjectIdeas
                  .Where(i => i.UserId == userId)
                  .OrderByDescending(i => i.CreatedAt)
                  .FirstOrDefaultAsync();

        if (Idea == null)
        {
            return;
        }

        LatestReview = await db.AiOutputReviews
            .AsNoTracking()
            .Where(r =>
                r.ProjectIdeaId == Idea.Id &&
                r.UserId == userId &&
                r.AgentName == "ProjectRoadmapAgent")
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync();

        var roadmap = await db.ProjectRoadmaps
            .Include(r => r.Phases)
            .FirstOrDefaultAsync(r => r.IdeaId == Idea.Id && r.UserId == userId);

        var allPhases = roadmap?.Phases
            .OrderBy(p => p.PhaseNumber)
            .ToList() ?? [];

        var deferredPhase = allPhases.FirstOrDefault(p => p.Name == DeferredScopePhaseMarker);
        Phases = allPhases.Where(p => p.Name != DeferredScopePhaseMarker).ToList();
        DeferredTasks = deferredPhase != null ? ParseDeferredTasks(deferredPhase.TasksJson) : [];

        if (Phases.Count > 0)
        {
            var profile = await db.StudentProfiles
                .AsNoTracking()
                .FirstOrDefaultAsync(p => p.UserId == userId);

            WorkloadSummary = ComputeWorkloadSummary(Idea, profile, Phases, DeferredTasks);
        }
    }

    /// <summary>
    /// Parses one phase's TasksJson defensively: the new structured shape
    /// (object per task, with hours/assignedMembers) if present, otherwise
    /// falls back to the old plain string[] shape used by roadmaps saved
    /// before this improvement -- so old saved roadmaps keep rendering
    /// instead of throwing on deserialize.
    /// </summary>
    public static List<RoadmapTaskView> ParseTasks(string tasksJson)
    {
        if (string.IsNullOrWhiteSpace(tasksJson) || tasksJson == "[]")
        {
            return [];
        }

        try
        {
            var structured = JsonSerializer.Deserialize<List<RoadmapTaskDto>>(
                tasksJson,
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true });

            if (structured is { Count: > 0 } && structured[0].Title is { Length: > 0 })
            {
                return structured
                    .Select(t => new RoadmapTaskView(
                        t.Title,
                        t.EstimatedHours,
                        t.EstimatedWorkingDays,
                        t.AssignedMembers ?? [],
                        t.Dependencies ?? [],
                        t.Complexity,
                        t.Priority,
                        t.MemberAllocations ?? []))
                    .ToList();
            }
        }
        catch (JsonException)
        {
            // Falls through to the plain-string-list parse below.
        }

        var plain = JsonSerializer.Deserialize<List<string>>(tasksJson) ?? [];
        return plain
            .Select(title => new RoadmapTaskView(title, null, null, [], [], null, null, []))
            .ToList();
    }

    /// <summary>
    /// Parses the synthetic deferred-scope phase's TasksJson (a
    /// RoadmapDeferredTaskDto[] blob, a different shape from a normal
    /// phase's task list). Defensive: an empty/malformed blob just yields
    /// no deferred tasks rather than throwing.
    /// </summary>
    public static List<RoadmapDeferredTaskView> ParseDeferredTasks(string tasksJson)
    {
        if (string.IsNullOrWhiteSpace(tasksJson) || tasksJson == "[]")
        {
            return [];
        }

        try
        {
            var structured = JsonSerializer.Deserialize<List<RoadmapDeferredTaskDto>>(
                tasksJson,
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true }) ?? [];

            return structured
                .Select(t => new RoadmapDeferredTaskView(
                    t.Title, t.Description, t.EstimatedHours, t.ReasonDeferred,
                    t.OriginalPhase, t.Priority))
                .ToList();
        }
        catch (JsonException)
        {
            return [];
        }
    }

    private static RoadmapWorkloadSummaryView ComputeWorkloadSummary(
        ProjectIdea idea,
        StudentProfile? profile,
        List<RoadmapPhase> phases,
        List<RoadmapDeferredTaskView> deferredTasks
    )
    {
        var teamSize = Math.Max(1, profile?.TeamMembers ?? 1);
        var hoursPerWeek = Math.Max(1, profile?.AvailableHoursPerWeek ?? 10);
        var totalWeeks = Math.Max(1, idea.ExpectedDurationWeeks > 0 ? idea.ExpectedDurationWeeks : 10);

        // `phases` here already excludes the deferred-scope phase (see
        // LoadPageDataAsync), so totalPlannedHours below is naturally the
        // ADJUSTED (post-deferral) figure -- what's actually still in the
        // plan -- while originalPlannedHours adds the deferred hours back
        // for comparison.
        var allTasks = phases.SelectMany(p => ParseTasks(p.TasksJson)).ToList();
        var adjustedPlannedHours = allTasks.Sum(t => t.EstimatedHours ?? 0);
        var deferredHours = deferredTasks.Sum(t => t.EstimatedHours);
        var originalPlannedHours = adjustedPlannedHours + deferredHours;

        var totalCapacityHours = totalWeeks * hoursPerWeek * teamSize;
        var utilization = totalCapacityHours > 0
            ? Math.Round(adjustedPlannedHours * 100.0 / totalCapacityHours, 1)
            : 0.0;

        var memberCapacity = totalWeeks * hoursPerWeek;
        var workloadByMember = new List<RoadmapMemberWorkloadView>();

        for (var i = 1; i <= teamSize; i++)
        {
            var label = $"Member {i}";
            var memberTasks = allTasks.Where(t => t.AssignedMembers.Contains(label)).ToList();
            // Use this member's own allocated share (MemberAllocations),
            // never the task's full EstimatedHours -- a collaborative task
            // lists BOTH members in AssignedMembers, so summing full hours
            // per member here would double-count exactly like the bug this
            // batch fixed on the Python side (see roadmap_scheduler.
            // allocate_task_hours). Falls back to full hours only for
            // roadmaps saved before MemberAllocations existed.
            var memberHours = memberTasks.Sum(t =>
            {
                var allocation = t.MemberAllocations.FirstOrDefault(a => a.MemberId == label);
                return allocation?.AllocatedHours ?? (t.EstimatedHours ?? 0);
            });
            var memberUtilization = memberCapacity > 0
                ? Math.Round(memberHours * 100.0 / memberCapacity, 1)
                : 0.0;

            workloadByMember.Add(new RoadmapMemberWorkloadView(
                label, memberTasks.Count, memberHours, memberUtilization));
        }

        var overloadHours = Math.Max(0, adjustedPlannedHours - totalCapacityHours);
        string scheduleFeasibility;
        int recommendedAdditionalWeeks;

        if (overloadHours > 0)
        {
            scheduleFeasibility = "over_capacity";
            var teamWeeklyCapacity = Math.Max(1, hoursPerWeek * teamSize);
            recommendedAdditionalWeeks = (int)Math.Ceiling(overloadHours / (double)teamWeeklyCapacity);
        }
        else if (deferredHours > 0)
        {
            scheduleFeasibility = "feasible_after_scope_reduction";
            recommendedAdditionalWeeks = 0;
        }
        else
        {
            scheduleFeasibility = "feasible";
            recommendedAdditionalWeeks = 0;
        }

        var warnings = new List<string>();
        if (allTasks.Count > 0)
        {
            if (scheduleFeasibility == "over_capacity")
            {
                warnings.Add(
                    $"Essential project scope exceeds available capacity by {overloadHours}h " +
                    $"even after deferring optional work -- add approximately " +
                    $"{recommendedAdditionalWeeks} more week(s) or reduce the mandatory scope.");
            }
            else if (scheduleFeasibility == "feasible_after_scope_reduction")
            {
                warnings.Add(
                    $"{deferredTasks.Count} optional task(s) totalling {deferredHours}h were " +
                    "deferred to future enhancements to fit the available capacity.");
            }
            else if (utilization < 30)
            {
                warnings.Add(
                    $"Planned work only uses {utilization}% of available capacity.");
            }
        }

        return new RoadmapWorkloadSummaryView(
            totalWeeks, teamSize, hoursPerWeek, totalCapacityHours,
            adjustedPlannedHours, utilization, workloadByMember, warnings,
            scheduleFeasibility, originalPlannedHours, adjustedPlannedHours,
            deferredHours, overloadHours, recommendedAdditionalWeeks);
    }

    private static ProjectRoadmapRequest BuildRoadmapRequest(
        ProjectIdea idea,
        StudentProfile? profile,
        List<StudentSkill> studentSkills
    )
    {
        var skillNames = studentSkills
            .Select(s => s.SkillName)
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var skillRatings = studentSkills
            .Where(s => !string.IsNullOrWhiteSpace(s.SkillName))
            .GroupBy(s => s.SkillName.Trim(), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(
                g => g.Key,
                g => Math.Clamp(g.First().Rating, 1, 5),
                StringComparer.OrdinalIgnoreCase
            );

        var expectedWeeks = idea.ExpectedDurationWeeks > 0
            ? idea.ExpectedDurationWeeks
            : 10;

        return new ProjectRoadmapRequest(
            IdeaTitle: idea.Title,
            ProblemStatement: idea.ProblemStatement,
            RequiredTechnologies: idea.RequiredTechnologies,
            RequiredSkills: idea.RequiredSkills,
            MissingSkills: idea.MissingSkills,
            DifficultyLevel: idea.DifficultyLevel,
            ExpectedDurationWeeks: expectedWeeks,
            Domain: idea.Domain,
            FinalDeliverables: idea.FinalDeliverables,
            TeamSize: profile?.TeamMembers ?? 1,
            AvailableHoursPerWeek: profile?.AvailableHoursPerWeek ?? 10,
            StudentSkills: skillNames,
            SkillRatings: skillRatings
        );
    }

    private static List<RoadmapPhase> ConvertAiRoadmapToPhases(
        ProjectRoadmapDto roadmap,
        string requiredTechnologies
    )
    {
        if (roadmap.Phases is { Count: > 0 })
        {
            var weeksByNumber = roadmap.Weeks.ToDictionary(w => w.WeekNumber);

            var phases = roadmap.Phases
                .Select((phase, index) =>
                {
                    // The last week within this phase's span still carries
                    // the risk/checkpoint narrative -- phases[] itself
                    // doesn't repeat those per-phase, so borrow them from
                    // weeks[] for display continuity.
                    weeksByNumber.TryGetValue(phase.EndWeek, out var lastWeek);

                    return new RoadmapPhase
                    {
                        PhaseNumber = index + 1,
                        Name = phase.Name,
                        Objective = phase.Objective,
                        TasksJson = JsonSerializer.Serialize(phase.Tasks ?? []),
                        ExpectedOutput = string.Join("; ", phase.Deliverables ?? []),
                        ToolsNeeded = requiredTechnologies,
                        // The real per-phase duration computed by
                        // roadmap_scheduler -- no longer hardcoded to 1.
                        EstimatedWeeks = phase.DurationWeeks,
                        Dependencies = phase.Dependencies is { Count: > 0 }
                            ? string.Join(", ", phase.Dependencies)
                            : "None",
                        Risks = lastWeek?.RiskWarning ?? "",
                        SuccessCriteria = lastWeek?.Checkpoint ?? "",
                        IsCompleted = false
                    };
                })
                .ToList();

            // Persist deferred tasks as one synthetic phase reusing the
            // existing Name/TasksJson/EstimatedWeeks(=0) columns -- no
            // migration needed, and tasks are never silently dropped.
            // RoadmapModel excludes this from the normal phase list and
            // parses it separately via ParseDeferredTasks.
            if (roadmap.DeferredTasks is { Count: > 0 })
            {
                phases.Add(new RoadmapPhase
                {
                    PhaseNumber = phases.Count + 1,
                    Name = RoadmapModel.DeferredScopePhaseMarker,
                    Objective = "",
                    TasksJson = JsonSerializer.Serialize(roadmap.DeferredTasks),
                    ExpectedOutput = "",
                    ToolsNeeded = requiredTechnologies,
                    EstimatedWeeks = 0,
                    Dependencies = "None",
                    Risks = "",
                    SuccessCriteria = "",
                    IsCompleted = false
                });
            }

            return phases;
        }

        // Backward-compatible fallback: only reachable if the AI service
        // response somehow lacks structured `phases` (older cached
        // response shape) -- keeps existing saved-roadmap replay safe
        // without assuming the new field is always present.
        return roadmap.Weeks
            .OrderBy(w => w.WeekNumber)
            .Select(week => new RoadmapPhase
            {
                PhaseNumber = week.WeekNumber,
                Name = week.PhaseTitle,
                Objective = week.MainGoal,
                TasksJson = JsonSerializer.Serialize(week.Tasks ?? []),
                ExpectedOutput = string.Join("; ", week.Deliverables ?? []),
                ToolsNeeded = requiredTechnologies,
                EstimatedWeeks = 1,
                Dependencies = week.WeekNumber == 1
                    ? "None"
                    : $"Complete Phase {week.WeekNumber - 1}",
                Risks = week.RiskWarning,
                SuccessCriteria = week.Checkpoint,
                IsCompleted = false
            })
            .ToList();
    }

    private int UserId()
        => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}