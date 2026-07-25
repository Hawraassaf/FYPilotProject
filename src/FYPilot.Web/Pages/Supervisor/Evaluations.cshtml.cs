using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class EvaluationsModel(
    ApplicationDbContext db) : PageModel
{
    public List<EvalRow> Evals { get; private set; } = [];

    public sealed record EvalRow(
        int ProjectId,
        int IdeaId,
        string ProjectTitle,
        string ProjectMembers,
        int ProjectMemberCount,
        string IdeaTitle,
        string Domain,
        string Status,
        int OriginalityScore,
        int SimilarityScore,
        string Comment,
        string ImprovementSuggestions,
        DateTime UpdatedAt);

    public async Task OnGetAsync(
        CancellationToken cancellationToken)
    {
        var supervisorId = SupervisorId();

        var projects = await db.Projects
            .AsNoTracking()
            .Include(project => project.Student)
            .Include(project => project.ProjectIdea)
            .Include(project => project.Members
                .Where(member => member.Status == "active"))
                .ThenInclude(member => member.User)
            .Where(project =>
                project.SupervisorId == supervisorId &&
                project.SupervisorAssignmentStatus == "active" &&
                project.ProjectIdeaId.HasValue &&
                project.ProjectIdea != null)
            .AsSplitQuery()
            .ToListAsync(cancellationToken);

        var projectIds = projects
            .Select(project => project.Id)
            .ToList();

        if (projectIds.Count == 0)
        {
            Evals = [];
            return;
        }

        var evaluations = await db.SupervisorEvaluations
            .AsNoTracking()
            .Where(evaluation =>
                evaluation.SupervisorId == supervisorId &&
                evaluation.ProjectId.HasValue &&
                projectIds.Contains(evaluation.ProjectId.Value))
            .OrderByDescending(evaluation =>
                evaluation.UpdatedAt == default
                    ? evaluation.CreatedAt
                    : evaluation.UpdatedAt)
            .ToListAsync(cancellationToken);

        var latestEvaluationByProject = evaluations
            .GroupBy(evaluation => evaluation.ProjectId!.Value)
            .ToDictionary(
                group => group.Key,
                group => group
                    .OrderByDescending(evaluation =>
                        evaluation.UpdatedAt == default
                            ? evaluation.CreatedAt
                            : evaluation.UpdatedAt)
                    .First());

        Evals = projects
            .Where(project =>
                latestEvaluationByProject.ContainsKey(project.Id))
            .Select(project =>
            {
                var evaluation =
                    latestEvaluationByProject[project.Id];

                var members = project.Members
                    .Where(member =>
                        member.Status == "active" &&
                        member.User != null)
                    .Select(member => new MemberSummary(
                        member.UserId,
                        member.User!.FullName))
                    .ToList();

                if (members.All(member =>
                        member.UserId != project.StudentId))
                {
                    members.Add(new MemberSummary(
                        project.StudentId,
                        project.Student?.FullName ?? "Project owner"));
                }

                var distinctMembers = members
                    .GroupBy(member => member.UserId)
                    .Select(group => group.First())
                    .OrderBy(member => member.FullName)
                    .ToList();

                var idea = project.ProjectIdea!;

                return new EvalRow(
                    ProjectId: project.Id,
                    IdeaId: idea.Id,
                    ProjectTitle: SafeProjectTitle(project.Title),
                    ProjectMembers: string.Join(
                        ", ",
                        distinctMembers.Select(member => member.FullName)),
                    ProjectMemberCount: distinctMembers.Count,
                    IdeaTitle: idea.Title,
                    Domain: string.IsNullOrWhiteSpace(idea.Domain)
                        ? "Uncategorized"
                        : idea.Domain,
                    Status: NormalizeStatus(evaluation.Status),
                    OriginalityScore: Math.Clamp(
                        evaluation.OriginalityScore,
                        0,
                        100),
                    SimilarityScore: Math.Clamp(
                        evaluation.SimilarityScore,
                        0,
                        100),
                    Comment: evaluation.Comment ?? "",
                    ImprovementSuggestions:
                        evaluation.ImprovementSuggestions ?? "",
                    UpdatedAt: evaluation.UpdatedAt == default
                        ? evaluation.CreatedAt
                        : evaluation.UpdatedAt);
            })
            .OrderByDescending(row => row.UpdatedAt)
            .ToList();
    }

    private static string NormalizeStatus(string? status)
    {
        var normalized = string.IsNullOrWhiteSpace(status)
            ? "pending"
            : status.Trim().ToLowerInvariant();

        return normalized switch
        {
            "approved" => "approved",
            "needs_revision" => "needs_revision",
            "rejected" => "rejected",
            _ => "pending"
        };
    }

    private static string SafeProjectTitle(string? title)
    {
        return string.IsNullOrWhiteSpace(title)
            ? "Untitled Project"
            : title.Trim();
    }

    private int SupervisorId()
    {
        var value = User
            .FindFirst(ClaimTypes.NameIdentifier)
            ?.Value;

        if (int.TryParse(value, out var supervisorId))
        {
            return supervisorId;
        }

        throw new InvalidOperationException(
            "Unable to identify the logged-in supervisor.");
    }

    private sealed record MemberSummary(
        int UserId,
        string FullName);
}