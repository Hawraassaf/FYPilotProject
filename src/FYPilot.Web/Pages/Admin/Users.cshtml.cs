using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class UsersModel(ApplicationDbContext db) : PageModel
{
    private const int SupervisorCapacity = 4;

    public List<StudentUserRow> Students { get; private set; } = [];
    public List<SupervisorUserRow> Supervisors { get; private set; } = [];

    public int TotalStudents { get; private set; }
    public int TotalSupervisors { get; private set; }
    public int StudentsWithoutSupervisor { get; private set; }
    public int FullSupervisors { get; private set; }

    public record StudentUserRow(
        int Id,
        string Name,
        string Email,
        DateTime JoinedAt,
        string AssignedSupervisor,
        string AssignmentStatus,
        int IdeasCount
    );

    public record AssignedStudentMini(
        int StudentId,
        string Name,
        string Email,
        DateTime AssignedAt
    );

    public record SupervisorUserRow(
        int Id,
        string Name,
        string Email,
        DateTime JoinedAt,
        int AssignedStudentsCount,
        int Capacity,
        string CapacityStatus,
        string CapacityClass,
        List<AssignedStudentMini> AssignedStudents
    );

    public async Task OnGetAsync()
    {
        var users = await db.Users
            .AsNoTracking()
            .OrderBy(u => u.FullName)
            .ToListAsync();

        var assignments = await db.SupervisorAssignments
            .AsNoTracking()
            .Include(a => a.Student)
            .Include(a => a.Supervisor)
            .OrderByDescending(a => a.UpdatedAt)
            .ToListAsync();

        var ideaCounts = await db.ProjectIdeas
            .AsNoTracking()
            .GroupBy(i => i.UserId)
            .Select(g => new
            {
                UserId = g.Key,
                Count = g.Count()
            })
            .ToDictionaryAsync(x => x.UserId, x => x.Count);

        var activeAssignments = assignments
            .Where(a => a.Status == "active")
            .ToList();

        var latestAssignmentByStudent = assignments
            .GroupBy(a => a.StudentId)
            .ToDictionary(
                g => g.Key,
                g => g.OrderByDescending(a => a.UpdatedAt).First()
            );

        Students = users
            .Where(u => u.Role == "student")
            .Select(student =>
            {
                latestAssignmentByStudent.TryGetValue(student.Id, out var assignment);

                var supervisorName = assignment?.Status == "active"
                    ? assignment.Supervisor?.FullName ?? "Assigned supervisor"
                    : "Not assigned";

                var assignmentStatus = assignment == null
                    ? "Unassigned"
                    : assignment.Status switch
                    {
                        "active" => "Assigned",
                        "pending_admin" => "Pending Approval",
                        "rejected" => "Rejected",
                        _ => "Unassigned"
                    };

                return new StudentUserRow(
                    student.Id,
                    student.FullName,
                    student.Email,
                    student.CreatedAt,
                    supervisorName,
                    assignmentStatus,
                    ideaCounts.TryGetValue(student.Id, out var count) ? count : 0
                );
            })
            .OrderBy(s => s.AssignmentStatus == "Unassigned" ? 0 : 1)
            .ThenBy(s => s.Name)
            .ToList();

        Supervisors = users
            .Where(u => u.Role == "supervisor")
            .Select(supervisor =>
            {
                var assignedStudents = activeAssignments
                    .Where(a => a.SupervisorId == supervisor.Id && a.Student != null)
                    .OrderBy(a => a.Student!.FullName)
                    .Select(a => new AssignedStudentMini(
                        a.StudentId,
                        a.Student!.FullName,
                        a.Student.Email,
                        a.ApprovedAt ?? a.UpdatedAt
                    ))
                    .ToList();

                var assignedCount = assignedStudents.Count;

                var status = assignedCount >= SupervisorCapacity
                    ? "Full"
                    : assignedCount >= SupervisorCapacity - 1
                        ? "Almost Full"
                        : "Available";

                var statusClass = assignedCount >= SupervisorCapacity
                    ? "danger"
                    : assignedCount >= SupervisorCapacity - 1
                        ? "warning"
                        : "success";

                return new SupervisorUserRow(
                    supervisor.Id,
                    supervisor.FullName,
                    supervisor.Email,
                    supervisor.CreatedAt,
                    assignedCount,
                    SupervisorCapacity,
                    status,
                    statusClass,
                    assignedStudents
                );
            })
            .OrderByDescending(s => s.AssignedStudentsCount)
            .ThenBy(s => s.Name)
            .ToList();

        TotalStudents = Students.Count;
        TotalSupervisors = Supervisors.Count;
        StudentsWithoutSupervisor = Students.Count(s => s.AssignmentStatus == "Unassigned");
        FullSupervisors = Supervisors.Count(s => s.AssignedStudentsCount >= SupervisorCapacity);
    }
}