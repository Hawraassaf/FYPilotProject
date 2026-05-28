using Microsoft.EntityFrameworkCore;
using FYPilot.Domain.Entities;

namespace FYPilot.Infrastructure.Data;

public class ApplicationDbContext(DbContextOptions<ApplicationDbContext> options) : DbContext(options)
{
    // Core user entities
    public DbSet<User> Users { get; set; }
    public DbSet<StudentProfile> StudentProfiles { get; set; }
    public DbSet<SupervisorProfile> SupervisorProfiles { get; set; }
    public DbSet<CompanyProfile> CompanyProfiles { get; set; }

    // Legacy project management
    public DbSet<Project> Projects { get; set; }
    public DbSet<ProjectTask> Tasks { get; set; }
    public DbSet<Milestone> Milestones { get; set; }
    public DbSet<Feedback> Feedbacks { get; set; }
    public DbSet<Challenge> Challenges { get; set; }
    public DbSet<Activity> Activities { get; set; }

    // FYPilot core
    public DbSet<StudentSkill> StudentSkills { get; set; }
    public DbSet<ProjectIdea> ProjectIdeas { get; set; }
    public DbSet<FeasibilityReport> FeasibilityReports { get; set; }
    public DbSet<ProjectRoadmap> ProjectRoadmaps { get; set; }
    public DbSet<RoadmapPhase> RoadmapPhases { get; set; }
    public DbSet<ChatMessage> ChatMessages { get; set; }
    public DbSet<MarketNeed> MarketNeeds { get; set; }
    public DbSet<PreviousProject> PreviousProjects { get; set; }
    public DbSet<SupervisorEvaluation> SupervisorEvaluations { get; set; }
    public DbSet<ProjectDocumentation> ProjectDocumentations => Set<ProjectDocumentation>();
    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);

        modelBuilder.Entity<User>(e =>
        {
            e.HasIndex(u => u.Email).IsUnique();
            e.Property(u => u.Role).HasDefaultValue("student");
        });

        modelBuilder.Entity<Project>(e =>
        {
            e.HasOne(p => p.Student)
                .WithMany(u => u.Projects)
                .HasForeignKey(p => p.StudentId)
                .OnDelete(DeleteBehavior.Cascade);

            e.HasOne(p => p.Supervisor)
                .WithMany()
                .HasForeignKey(p => p.SupervisorId)
                .OnDelete(DeleteBehavior.SetNull);
        });

        modelBuilder.Entity<Feedback>(e =>
        {
            e.HasOne(f => f.Supervisor)
                .WithMany()
                .HasForeignKey(f => f.SupervisorId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        modelBuilder.Entity<Challenge>(e =>
        {
            e.HasOne(c => c.Company)
                .WithMany()
                .HasForeignKey(c => c.CompanyId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        modelBuilder.Entity<ProjectIdea>(e =>
        {
            e.HasOne(i => i.User)
                .WithMany()
                .HasForeignKey(i => i.UserId)
                .OnDelete(DeleteBehavior.Cascade);

            e.HasOne(i => i.FeasibilityReport)
                .WithOne(f => f.Idea)
                .HasForeignKey<FeasibilityReport>(f => f.IdeaId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        modelBuilder.Entity<ProjectRoadmap>(e =>
        {
            e.HasOne(r => r.Idea)
                .WithMany()
                .HasForeignKey(r => r.IdeaId)
                .OnDelete(DeleteBehavior.Cascade);

            e.HasMany(r => r.Phases)
                .WithOne(p => p.Roadmap)
                .HasForeignKey(p => p.RoadmapId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        modelBuilder.Entity<SupervisorEvaluation>(e =>
        {
            e.HasOne(se => se.Idea)
                .WithMany()
                .HasForeignKey(se => se.IdeaId)
                .OnDelete(DeleteBehavior.Cascade);

            e.HasOne(se => se.Supervisor)
                .WithMany()
                .HasForeignKey(se => se.SupervisorId)
                .OnDelete(DeleteBehavior.Restrict);
        });
        modelBuilder.Entity<ProjectDocumentation>(entity =>
        {
            entity.ToTable("project_documentations");

            entity.HasKey(e => e.Id);

            entity.Property(e => e.Title).IsRequired();
            entity.Property(e => e.FunctionalRequirementsJson).IsRequired();
            entity.Property(e => e.NonFunctionalRequirementsJson).IsRequired();
            entity.Property(e => e.UseCasesJson).IsRequired();
            entity.Property(e => e.EdgeCasesJson).IsRequired();
            entity.Property(e => e.DatabaseDesignJson).IsRequired();
            entity.Property(e => e.UiDesignJson).IsRequired();
            entity.Property(e => e.DiagramDescriptionsJson).IsRequired();
            entity.Property(e => e.AiTechnicalReportJson).IsRequired();
            entity.Property(e => e.SupervisorStatus).IsRequired();
        });
    }
}
