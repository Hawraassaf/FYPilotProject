using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Services.Supervisors;

public class SupervisorAccessService(ApplicationDbContext db)
{
    public async Task<List<int>> GetAssignedStudentIdsAsync(int supervisorId)
    {
        return await db.SupervisorAssignments
            .AsNoTracking()
            .Where(a =>
                a.SupervisorId == supervisorId &&
                a.Status == "active")
            .Select(a => a.StudentId)
            .ToListAsync();
    }

    public async Task<bool> CanAccessStudentAsync(int supervisorId, int studentId)
    {
        return await db.SupervisorAssignments
            .AsNoTracking()
            .AnyAsync(a =>
                a.SupervisorId == supervisorId &&
                a.StudentId == studentId &&
                a.Status == "active");
    }
}