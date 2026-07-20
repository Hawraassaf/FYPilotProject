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

    public DbSet<ProjectMember> ProjectMembers =>
        Set<ProjectMember>();

    public DbSet<ProjectInvitation> ProjectInvitations =>
        Set<ProjectInvitation>();

    public DbSet<TeammateRequest> TeammateRequests =>
        Set<TeammateRequest>();

    public DbSet<ProjectTask> Tasks { get; set; }
    public DbSet<Milestone> Milestones { get; set; }
    public DbSet<Feedback> Feedbacks { get; set; }
    public DbSet<Challenge> Challenges { get; set; }
    public DbSet<Activity> Activities { get; set; }

    // Market Demand Analysis (real-time AI: search + local LLM)
    public DbSet<MarketDemandAnalysis> MarketDemandAnalysis =>
        Set<MarketDemandAnalysis>();
    public DbSet<MarketDemandSource> MarketDemandSources =>
        Set<MarketDemandSource>();
    public DbSet<MarketSimilarSolution> MarketSimilarSolutions =>
        Set<MarketSimilarSolution>();
    public DbSet<MarketTrendSignal> MarketTrendSignals =>
        Set<MarketTrendSignal>();
    public DbSet<MarketDemandYearlyPoint> MarketDemandYearlyPoints =>
        Set<MarketDemandYearlyPoint>();

    // Mentor Chat
    public DbSet<MentorChatSession> MentorChatSessions => Set<MentorChatSession>();

    // AI Output Review Pipeline (services/FYPilot.AI/app/review/pipeline.py)
    public DbSet<AiOutputReview> AiOutputReviews => Set<AiOutputReview>();

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
    public DbSet<Meeting> Meetings { get; set; }
    public DbSet<ProjectDocumentation> ProjectDocumentations => Set<ProjectDocumentation>();
    public DbSet<MarketOpportunitySnapshot> MarketOpportunitySnapshots => Set<MarketOpportunitySnapshot>();
    public DbSet<MarketOpportunityRegion> MarketOpportunityRegions => Set<MarketOpportunityRegion>();
    public DbSet<PasswordResetToken> PasswordResetTokens => Set<PasswordResetToken>();
    public DbSet<FeedbackMessage> FeedbackMessages => Set<FeedbackMessage>();

    // Supervisor Assignment & Notifications
    public DbSet<SupervisorPreferenceBatch> SupervisorPreferenceBatches { get; set; }
    public DbSet<SupervisorPreference> SupervisorPreferences { get; set; }
    public DbSet<SupervisorAssignment> SupervisorAssignments { get; set; }
    public DbSet<Notification> Notifications { get; set; }

    // Google Calendar Integration
    public DbSet<GoogleCalendarToken> GoogleCalendarTokens => Set<GoogleCalendarToken>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);

        modelBuilder.Entity<User>(e =>
        {
            e.HasIndex(u => u.Email).IsUnique();
            e.Property(u => u.Role).HasDefaultValue("student");
        });

        modelBuilder.Entity<SupervisorProfile>(entity =>
        {
            entity.ToTable("supervisor_profiles");

            entity.HasKey(e => e.Id);

            entity.Property(e => e.Id).HasColumnName("id");
            entity.Property(e => e.UserId).HasColumnName("user_id");
            entity.Property(e => e.AcademicTitle).HasColumnName("academic_title").HasMaxLength(100);
            entity.Property(e => e.Department).HasColumnName("department").HasMaxLength(150);
            entity.Property(e => e.Faculty).HasColumnName("faculty").HasMaxLength(150);
            entity.Property(e => e.University).HasColumnName("university").HasMaxLength(150);
            entity.Property(e => e.Specialization).HasColumnName("specialization").HasMaxLength(200);
            entity.Property(e => e.ResearchAreas).HasColumnName("research_areas");
            entity.Property(e => e.OfficeLocation).HasColumnName("office_location").HasMaxLength(150);
            entity.Property(e => e.OfficeHours).HasColumnName("office_hours").HasMaxLength(150);
            entity.Property(e => e.PreferredMeetingMode).HasColumnName("preferred_meeting_mode").HasMaxLength(80);
            entity.Property(e => e.Bio).HasColumnName("bio");
            entity.Property(e => e.LinkedInUrl).HasColumnName("linkedin_url").HasMaxLength(300);
            entity.Property(e => e.WebsiteUrl).HasColumnName("website_url").HasMaxLength(300);
            entity.Property(e => e.ProfileImagePath).HasColumnName("profile_image_path").HasMaxLength(500);
            entity.Property(e => e.UpdatedAt).HasColumnName("updated_at");

            entity.HasOne(e => e.User)
                .WithOne(u => u.SupervisorProfile)
                .HasForeignKey<SupervisorProfile>(e => e.UserId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasIndex(e => e.UserId).IsUnique();
        });

        modelBuilder.Entity<Project>(e =>
        {
            e.Property(p => p.MaximumMembers)
                .HasDefaultValue(3);

            e.HasOne(p => p.Student)
                .WithMany(u => u.Projects)
                .HasForeignKey(p => p.StudentId)
                .OnDelete(DeleteBehavior.Cascade);

            e.HasOne(p => p.Supervisor)
                .WithMany()
                .HasForeignKey(p => p.SupervisorId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasOne(p => p.ProjectIdea)
                .WithOne(i => i.Project)
                .HasForeignKey<Project>(p => p.ProjectIdeaId)
                .OnDelete(DeleteBehavior.Restrict);

            e.HasIndex(p => p.ProjectIdeaId)
                .IsUnique();
        });
        modelBuilder.Entity<ProjectMember>(e =>
        {
            e.Property(member => member.Role)
                .HasMaxLength(30)
                .HasDefaultValue("collaborator");

            e.Property(member => member.Status)
                .HasMaxLength(30)
                .HasDefaultValue("active");

            e.HasOne(member => member.Project)
                .WithMany(project => project.Members)
                .HasForeignKey(member => member.ProjectId)
                .OnDelete(DeleteBehavior.Cascade);

            e.HasOne(member => member.User)
                .WithMany(user => user.ProjectMemberships)
                .HasForeignKey(member => member.UserId)
                .OnDelete(DeleteBehavior.Restrict);

            e.HasIndex(member => new
            {
                member.ProjectId,
                member.UserId
            }).IsUnique();

            e.HasIndex(member => new
            {
                member.UserId,
                member.Status
            });
        });
        modelBuilder.Entity<ProjectInvitation>(e =>
        {
            e.Property(invitation => invitation.InvitedEmail)
                .HasMaxLength(256);

            e.Property(invitation => invitation.TokenHash)
                .HasMaxLength(64);

            e.Property(invitation => invitation.Status)
                .HasMaxLength(30)
                .HasDefaultValue("pending");

            e.Property(invitation => invitation.Source)
                .HasMaxLength(30)
                .HasDefaultValue("student_invite");

            e.HasOne(invitation => invitation.Project)
                .WithMany(project => project.Invitations)
                .HasForeignKey(invitation => invitation.ProjectId)
                .OnDelete(DeleteBehavior.Cascade);

            e.HasOne(invitation => invitation.InvitedByUser)
                .WithMany()
                .HasForeignKey(invitation => invitation.InvitedByUserId)
                .OnDelete(DeleteBehavior.Restrict);

            e.HasOne(invitation => invitation.InvitedUser)
                .WithMany()
                .HasForeignKey(invitation => invitation.InvitedUserId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasOne(invitation => invitation.TeammateRequest)
                .WithMany(request => request.Invitations)
                .HasForeignKey(invitation => invitation.TeammateRequestId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasIndex(invitation => invitation.TokenHash)
                .IsUnique();

            e.HasIndex(invitation => new
            {
                invitation.ProjectId,
                invitation.InvitedEmail,
                invitation.Status
            });
        });
        modelBuilder.Entity<TeammateRequest>(e =>
        {
            e.Property(request => request.Domain)
                .HasMaxLength(200);

            e.Property(request => request.Status)
                .HasMaxLength(30)
                .HasDefaultValue("pending");

            e.Property(request => request.RequestedMembersCount)
                .HasDefaultValue(1);

            e.HasOne(request => request.Project)
                .WithMany(project => project.TeammateRequests)
                .HasForeignKey(request => request.ProjectId)
                .OnDelete(DeleteBehavior.Cascade);

            e.HasOne(request => request.RequestedByUser)
                .WithMany()
                .HasForeignKey(request => request.RequestedByUserId)
                .OnDelete(DeleteBehavior.Restrict);

            e.HasOne(request => request.MatchedUser)
                .WithMany()
                .HasForeignKey(request => request.MatchedUserId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasOne(request => request.MatchedBySupervisor)
                .WithMany()
                .HasForeignKey(request => request.MatchedBySupervisorId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasIndex(request => new
            {
                request.ProjectId,
                request.Status
            });

            e.HasIndex(request => new
            {
                request.Domain,
                request.Status
            });
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

        modelBuilder.Entity<MarketDemandAnalysis>(entity =>
        {
            entity.ToTable("market_demand_analysis");
            entity.HasKey(x => x.Id);

            entity.Property(x => x.MarketDemand).HasMaxLength(50);
            entity.Property(x => x.TargetSector).HasMaxLength(300);
            entity.Property(x => x.CountryContext).HasMaxLength(120);
            entity.Property(x => x.Source).HasMaxLength(120);
            entity.Property(x => x.Provider).HasMaxLength(120);
            entity.Property(x => x.ModelUsed).HasMaxLength(200);
            entity.Property(x => x.SearchProvider).HasMaxLength(200);
            entity.Property(x => x.ConfidenceLevel).HasMaxLength(30);
            entity.Property(x => x.ForecastStatus).HasMaxLength(80);
            entity.Property(x => x.ForecastModel).HasMaxLength(120);
            entity.Property(x => x.TrendDirection).HasMaxLength(30);
            entity.Property(x => x.TrendStrength).HasMaxLength(30);

            // Preserve compatibility with the forecast migration already applied.
            entity.Property(x => x.TrendSlopePerYear)
                .HasColumnName("TrendSlopePerWeek");

            entity.HasOne(x => x.ProjectIdea)
                .WithMany()
                .HasForeignKey(x => x.ProjectIdeaId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasIndex(x => new
            {
                x.UserId,
                x.ProjectIdeaId,
                x.AnalyzedAt
            });
        });

        modelBuilder.Entity<MarketDemandYearlyPoint>(entity =>
        {
            entity.ToTable("market_demand_yearly_points");
            entity.HasKey(x => x.Id);

            entity.Property(x => x.DemandIndex)
                .HasPrecision(6, 2);
            entity.Property(x => x.EvidenceSummary)
                .HasColumnType("text");
            entity.Property(x => x.SourceUrlsJson)
                .HasColumnType("text");

            entity.HasOne(x => x.MarketDemandAnalysis)
                .WithMany(x => x.YearlyPoints)
                .HasForeignKey(x => x.MarketDemandAnalysisId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasIndex(x => new
            {
                x.MarketDemandAnalysisId,
                x.Year
            }).IsUnique();
        });

        modelBuilder.Entity<MarketDemandSource>(entity =>
        {
            entity.ToTable("market_demand_sources");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Title).HasMaxLength(500);
            entity.Property(x => x.Url).HasMaxLength(2000);
            entity.Property(x => x.Publisher).HasMaxLength(250);
            entity.Property(x => x.SourceType).HasMaxLength(100);
            entity.HasOne(x => x.MarketDemandAnalysis)
                .WithMany(x => x.Sources)
                .HasForeignKey(x => x.MarketDemandAnalysisId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        modelBuilder.Entity<MarketSimilarSolution>(entity =>
        {
            entity.ToTable("market_similar_solutions");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Name).HasMaxLength(300);
            entity.Property(x => x.Similarity).HasMaxLength(30);
            entity.HasOne(x => x.MarketDemandAnalysis)
                .WithMany(x => x.SimilarSolutions)
                .HasForeignKey(x => x.MarketDemandAnalysisId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        modelBuilder.Entity<MarketTrendSignal>(entity =>
        {
            entity.ToTable("market_trend_signals");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Topic).HasMaxLength(300);
            entity.Property(x => x.Direction).HasMaxLength(30);
            entity.Property(x => x.SourceUrl).HasMaxLength(2000);
            entity.HasOne(x => x.MarketDemandAnalysis)
                .WithMany(x => x.TrendSignals)
                .HasForeignKey(x => x.MarketDemandAnalysisId)
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

        modelBuilder.Entity<MentorChatSession>()
            .HasMany(s => s.Messages)
            .WithOne(m => m.MentorChatSession)
            .HasForeignKey(m => m.MentorChatSessionId)
            .OnDelete(DeleteBehavior.Cascade);

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

        modelBuilder.Entity<MarketOpportunitySnapshot>(entity =>
        {
            entity.ToTable("market_opportunity_snapshots");
            entity.HasKey(x => x.Id);

            entity.Property(x => x.Status).HasMaxLength(40);
            entity.Property(x => x.OverallDemandLevel).HasMaxLength(30);
            entity.Property(x => x.BestLaunchMarket).HasMaxLength(120);
            entity.Property(x => x.BestLaunchReason).HasMaxLength(2000);
            entity.Property(x => x.ExpansionPathJson).HasColumnType("text");
            entity.Property(x => x.WhyDemandedJson).HasColumnType("text");
            entity.Property(x => x.StrategicRecommendation).HasColumnType("text");
            entity.Property(x => x.LimitationsJson).HasColumnType("text");
            entity.Property(x => x.SourcesJson).HasColumnType("text");
            entity.Property(x => x.Provider).HasMaxLength(120);
            entity.Property(x => x.ModelUsed).HasMaxLength(200);

            entity.HasOne(x => x.ProjectIdea)
                .WithMany()
                .HasForeignKey(x => x.ProjectIdeaId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasOne(x => x.User)
                .WithMany()
                .HasForeignKey(x => x.UserId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasIndex(x => x.ProjectIdeaId);
            entity.HasIndex(x => x.UserId);
            entity.HasIndex(x => x.AnalyzedAt);

            // Efficiently loading "latest snapshot per idea" for several
            // ideas at once (Idea Generator) benefits from this composite
            // index ordered for a descending scan per idea.
            entity.HasIndex(x => new { x.ProjectIdeaId, x.AnalyzedAt });
        });

        modelBuilder.Entity<MarketOpportunityRegion>(entity =>
        {
            entity.ToTable("market_opportunity_regions");
            entity.HasKey(x => x.Id);

            entity.Property(x => x.RegionKey).HasMaxLength(20);
            entity.Property(x => x.RegionName).HasMaxLength(50);
            entity.Property(x => x.DemandLevel).HasMaxLength(30);
            entity.Property(x => x.CompetitionPressure).HasMaxLength(20);
            entity.Property(x => x.EvidenceSummary).HasColumnType("text");
            entity.Property(x => x.ScoreBreakdownJson).HasColumnType("text");
            entity.Property(x => x.SourceUrlsJson).HasColumnType("text");

            entity.HasOne(x => x.Snapshot)
                .WithMany(x => x.Regions)
                .HasForeignKey(x => x.SnapshotId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasIndex(x => new { x.SnapshotId, x.RegionKey }).IsUnique();
        });

        modelBuilder.Entity<PasswordResetToken>(entity =>
        {
            entity.ToTable("password_reset_tokens");

            entity.HasKey(e => e.Id);

            entity.Property(e => e.Id)
                .HasColumnName("id");

            entity.Property(e => e.UserId)
                .HasColumnName("user_id");

            entity.Property(e => e.TokenHash)
                .HasColumnName("token_hash")
                .IsRequired();

            entity.Property(e => e.ExpiresAt)
                .HasColumnName("expires_at");

            entity.Property(e => e.UsedAt)
                .HasColumnName("used_at");

            entity.Property(e => e.CreatedAt)
                .HasColumnName("created_at");

            entity.HasOne(e => e.User)
                .WithMany()
                .HasForeignKey(e => e.UserId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasIndex(e => e.TokenHash)
                .IsUnique();
        });

        // NOTE: SupervisorPreferenceBatch, SupervisorPreference,
        // SupervisorAssignment, Notification, and GoogleCalendarToken have
        // no explicit OnModelCreating configuration in either source version
        // — they were relying on EF Core default conventions (or being
        // configured elsewhere, e.g. IEntityTypeConfiguration classes, not
        // present in either file reviewed). Nothing was invented here; if
        // those entities need explicit table/column mapping, add it the
        // same way as the blocks above.
    }
}
