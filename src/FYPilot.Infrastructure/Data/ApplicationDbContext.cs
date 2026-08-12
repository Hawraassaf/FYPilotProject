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

    public DbSet<EmailVerificationCode>
        EmailVerificationCodes =>
            Set<EmailVerificationCode>();

    // Legacy project management
    public DbSet<Project> Projects { get; set; }

    public DbSet<ProjectMember> ProjectMembers =>
     Set<ProjectMember>();

    public DbSet<ProjectActivity> ProjectActivities =>
        Set<ProjectActivity>();
    public DbSet<ProjectDiscussionMessage>
    ProjectDiscussionMessages =>
        Set<ProjectDiscussionMessage>();
    public DbSet<ProjectDiscussionAttachment>
    ProjectDiscussionAttachments =>
        Set<ProjectDiscussionAttachment>();
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

    // Mentor Chat
    public DbSet<MentorChatSession> MentorChatSessions => Set<MentorChatSession>();

    // AI Output Review Pipeline (services/FYPilot.AI/app/review/pipeline.py)
    public DbSet<AiOutputReview> AiOutputReviews => Set<AiOutputReview>();

    // Centralized AI Agent Loading System -- durable job records the
    // AiAgentJobCoordinator background service drives to completion
    // independently of any connected browser (see AiAgentJobService).
    public DbSet<AiAgentJob> AiAgentJobs => Set<AiAgentJob>();
    public DbSet<ProjectDnaAnalysisRecord>
    ProjectDnaAnalyses =>
        Set<ProjectDnaAnalysisRecord>();
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

    // Idea Generation Knowledge Base (admin-curated institutional context
    // retrieved into ProjectIdeaAgent -- see AdminIdeaContextService.
    // Contextual retrieval and controlled prompting only; no model
    // training or fine-tuning is involved.)
    public DbSet<IdeaGenerationGuidance> IdeaGenerationGuidances => Set<IdeaGenerationGuidance>();
    public DbSet<HistoricalFypProject> HistoricalFypProjects => Set<HistoricalFypProject>();
    public DbSet<HistoricalFypFutureOpportunity> HistoricalFypFutureOpportunities => Set<HistoricalFypFutureOpportunity>();

    // Supervisor Assignment & Notifications
    public DbSet<SupervisorPreferenceBatch> SupervisorPreferenceBatches { get; set; }
    public DbSet<SupervisorPreference> SupervisorPreferences { get; set; }
    public DbSet<SupervisorAssignment> SupervisorAssignments { get; set; }
    public DbSet<Notification> Notifications { get; set; }

    // Supervisor Registration Requests -- pre-account Supervisor
    // applications. No User/SupervisorProfile exists until an Admin
    // approves the request (see SupervisorAssignments admin page).
    public DbSet<SupervisorRegistrationRequest> SupervisorRegistrationRequests =>
        Set<SupervisorRegistrationRequest>();

    // Google Calendar Integration
    public DbSet<GoogleCalendarToken> GoogleCalendarTokens => Set<GoogleCalendarToken>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);

        modelBuilder.Entity<User>(e =>
        {
            e.HasIndex(user => user.Email)
                .IsUnique();

            e.Property(user => user.Role)
                .HasDefaultValue("student");

            e.Property(user => user.IsMainAdmin)
                .HasColumnName("is_main_admin")
                .HasDefaultValue(false);

            e.Property(user => user.IsEmailVerified)
                .HasColumnName("is_email_verified")
                .HasDefaultValue(false);

            e.Property(user => user.EmailVerifiedAtUtc)
                .HasColumnName("email_verified_at_utc");

            /*
             * PostgreSQL partial unique index:
             * at most one row can be marked as the main administrator.
             */
            e.HasIndex(user => user.IsMainAdmin)
                .IsUnique()
                .HasFilter("\"is_main_admin\" = TRUE");

            e.Property(user => user.LastProjectPage)
                .HasMaxLength(200);

            e.HasOne(user => user.LastActiveProject)
                .WithMany()
                .HasForeignKey(user => user.LastActiveProjectId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasIndex(user => user.LastActiveProjectId);
        });

        modelBuilder.Entity<EmailVerificationCode>(entity =>
        {
            entity.ToTable("email_verification_codes");

            entity.HasKey(code => code.Id);

            entity.Property(code => code.Id)
                .HasColumnName("id");

            entity.Property(code => code.UserId)
                .HasColumnName("user_id");

            entity.Property(code => code.CodeHash)
                .HasColumnName("code_hash")
                .HasMaxLength(100)
                .IsRequired();

            entity.Property(code => code.CreatedAtUtc)
                .HasColumnName("created_at_utc");

            entity.Property(code => code.SentAtUtc)
                .HasColumnName("sent_at_utc");

            entity.Property(code => code.ExpiresAtUtc)
                .HasColumnName("expires_at_utc");

            entity.Property(code => code.UsedAtUtc)
                .HasColumnName("used_at_utc");

            entity.Property(code => code.FailedAttemptCount)
                .HasColumnName("failed_attempt_count")
                .HasDefaultValue(0);

            entity.HasOne(code => code.User)
                .WithMany(user =>
                    user.EmailVerificationCodes)
                .HasForeignKey(code => code.UserId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasIndex(code => code.UserId);

            entity.HasIndex(code =>
                code.ExpiresAtUtc);
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
            e.Property(project =>
              project.SupervisorAssignmentStatus)
              .HasMaxLength(40)
              .HasDefaultValue("unassigned");
            e.Property(p => p.MaximumMembers)
                .HasDefaultValue(3);
            e.Property(project => project.IsDeleted)
               .HasDefaultValue(false);

            e.HasOne(project => project.DeletedByUser)
                .WithMany()
                .HasForeignKey(project =>
                    project.DeletedByUserId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasIndex(project => new
            {
                project.IsDeleted,
                project.StudentId
            });
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
            e.Property(member => member.IsArchived)
                .HasDefaultValue(false);
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
                member.Status,
                member.IsArchived
            });
            e.HasIndex(member => new
            {
                member.ProjectId,
                member.Status
            });
        });
        modelBuilder.Entity<ProjectActivity>(e =>
        {
            e.Property(activity => activity.ActionType)
                .HasMaxLength(80)
                .IsRequired();

            e.Property(activity => activity.Description)
                .HasMaxLength(1000)
                .IsRequired();

            e.HasOne(activity => activity.Project)
                .WithMany(project => project.Activities)
                .HasForeignKey(activity => activity.ProjectId)
                .OnDelete(DeleteBehavior.Cascade);

            e.HasOne(activity => activity.User)
                .WithMany(user => user.ProjectActivities)
                .HasForeignKey(activity => activity.UserId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasOne(activity => activity.PreviousIdea)
                .WithMany()
                .HasForeignKey(activity => activity.PreviousIdeaId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasOne(activity => activity.NewIdea)
                .WithMany()
                .HasForeignKey(activity => activity.NewIdeaId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasIndex(activity => new
            {
                activity.ProjectId,
                activity.CreatedAtUtc
            });

            e.HasIndex(activity => activity.UserId);
        });
        modelBuilder.Entity<ProjectDiscussionMessage>(entity =>
        {
            entity.ToTable("project_discussion_messages");

            entity.Property(message => message.Content)
                .HasMaxLength(1000)
                .IsRequired();

            entity.HasOne(message => message.Project)
                .WithMany()
                .HasForeignKey(message => message.ProjectId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasOne(message => message.User)
                .WithMany()
                .HasForeignKey(message => message.UserId)
                .OnDelete(DeleteBehavior.Restrict);

            entity.HasOne(message => message.ReplyToMessage)
                .WithMany(message => message.Replies)
                .HasForeignKey(message => message.ReplyToMessageId)
                .OnDelete(DeleteBehavior.Restrict);

            entity.HasIndex(message => new
            {
                message.ProjectId,
                message.CreatedAtUtc
            });
        });

        modelBuilder.Entity<ProjectDiscussionAttachment>(entity =>
        {
            entity.ToTable("project_discussion_attachments");

            entity.Property(attachment =>
                    attachment.OriginalFileName)
                .HasMaxLength(255)
                .IsRequired();

            entity.Property(attachment =>
                    attachment.StoredFileName)
                .HasMaxLength(255)
                .IsRequired();

            entity.Property(attachment =>
                    attachment.ContentType)
                .HasMaxLength(150)
                .IsRequired();

            entity.Property(attachment =>
                    attachment.RelativePath)
                .HasMaxLength(500)
                .IsRequired();

            entity.HasOne(attachment => attachment.Message)
                .WithMany(message => message.Attachments)
                .HasForeignKey(attachment =>
                    attachment.MessageId)
                .OnDelete(DeleteBehavior.Cascade);
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

            e.HasOne(i => i.GeneratedForProject)
                .WithMany(project =>
                    project.GeneratedCandidateIdeas)
                .HasForeignKey(i =>
                    i.GeneratedForProjectId)
                .OnDelete(DeleteBehavior.SetNull);

            e.HasIndex(i => new
            {
                i.GeneratedForProjectId,
                i.CreatedAt
            });

            e.HasOne(i => i.FeasibilityReport)
                .WithOne(f => f.Idea)
                .HasForeignKey<FeasibilityReport>(
                    f => f.IdeaId)
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

        modelBuilder.Entity<SupervisorEvaluation>(entity =>
        {
            entity.Property(evaluation =>
                    evaluation.Status)
                .HasMaxLength(40)
                .HasDefaultValue("pending");

            entity.HasOne(evaluation =>
                    evaluation.Project)
                .WithMany(project =>
                    project.SupervisorEvaluations)
                .HasForeignKey(evaluation =>
                    evaluation.ProjectId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasOne(evaluation =>
                    evaluation.Idea)
                .WithMany()
                .HasForeignKey(evaluation =>
                    evaluation.IdeaId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasOne(evaluation =>
                    evaluation.Supervisor)
                .WithMany()
                .HasForeignKey(evaluation =>
                    evaluation.SupervisorId)
                .OnDelete(DeleteBehavior.Restrict);


            entity.HasIndex(evaluation => new
            {
                evaluation.ProjectId,
                evaluation.IdeaId
            })
                .IsUnique()
                .HasFilter(
                    "\"project_id\" IS NOT NULL");

            entity.HasIndex(evaluation => new
            {
                evaluation.SupervisorId,
                evaluation.ProjectId
            });
        });
        modelBuilder.Entity<SupervisorAssignment>(entity =>
        {
            entity.Property(assignment =>
                    assignment.Status)
                .HasMaxLength(40)
                .HasDefaultValue("pending_admin");

            entity.HasOne(assignment =>
                    assignment.Project)
                .WithMany(project =>
                    project.SupervisorAssignments)
                .HasForeignKey(assignment =>
                    assignment.ProjectId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasOne(assignment =>
                    assignment.Student)
                .WithMany()
                .HasForeignKey(assignment =>
                    assignment.StudentId)
                .OnDelete(DeleteBehavior.Restrict);

            entity.HasOne(assignment =>
                    assignment.Supervisor)
                .WithMany()
                .HasForeignKey(assignment =>
                    assignment.SupervisorId)
                .OnDelete(DeleteBehavior.Restrict);

            entity.HasOne(assignment =>
                    assignment.AssignedByAdmin)
                .WithMany()
                .HasForeignKey(assignment =>
                    assignment.AssignedByAdminId)
                .OnDelete(DeleteBehavior.SetNull);


            entity.HasIndex(assignment =>
                    assignment.ProjectId)
                .IsUnique()
                .HasFilter(
                    "\"project_id\" IS NOT NULL "
                    + "AND \"status\" IN "
                    + "('pending_admin', 'active')");

            entity.HasIndex(assignment => new
            {
                assignment.SupervisorId,
                assignment.Status
            });
        });
        modelBuilder.Entity<Meeting>(entity =>
        {
            entity.HasOne(meeting =>
                    meeting.Project)
                .WithMany(project =>
                    project.Meetings)
                .HasForeignKey(meeting =>
                    meeting.ProjectId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasOne(meeting =>
                    meeting.Supervisor)
                .WithMany()
                .HasForeignKey(meeting =>
                    meeting.SupervisorId)
                .OnDelete(DeleteBehavior.Restrict);

            entity.HasOne(meeting =>
                    meeting.Student)
                .WithMany()
                .HasForeignKey(meeting =>
                    meeting.StudentId)
                .OnDelete(DeleteBehavior.SetNull);

            entity.HasIndex(meeting => new
            {
                meeting.ProjectId,
                meeting.ScheduledAt
            });

            entity.HasIndex(meeting => new
            {
                meeting.SupervisorId,
                meeting.ScheduledAt
            });
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

        modelBuilder.Entity<IdeaGenerationGuidance>(entity =>
        {
            entity.ToTable("idea_generation_guidances");
            entity.HasKey(x => x.Id);

            entity.Property(x => x.Title).HasMaxLength(200).IsRequired();
            entity.Property(x => x.Content).HasMaxLength(4000).IsRequired();
            entity.Property(x => x.GuidanceType).HasMaxLength(40).IsRequired();
            entity.Property(x => x.Major).HasMaxLength(100);
            entity.Property(x => x.Domain).HasMaxLength(100);

            entity.HasOne(x => x.CreatedByUser)
                .WithMany()
                .HasForeignKey(x => x.CreatedByUserId)
                // Keep the guidance row and clear only the deleted
                // administrator reference.
                .OnDelete(DeleteBehavior.SetNull);

            entity.HasIndex(x => x.IsActive);
            entity.HasIndex(x => x.Major);
            entity.HasIndex(x => x.Domain);
            entity.HasIndex(x => x.Priority);
        });

        modelBuilder.Entity<HistoricalFypProject>(entity =>
        {
            entity.ToTable("historical_fyp_projects");
            entity.HasKey(x => x.Id);

            entity.Property(x => x.Title).HasMaxLength(200).IsRequired();
            entity.Property(x => x.ProblemStatement).HasMaxLength(4000).IsRequired();
            entity.Property(x => x.Major).HasMaxLength(100);
            entity.Property(x => x.Domain).HasMaxLength(100);
            entity.Property(x => x.TargetUsers).HasMaxLength(500);
            entity.Property(x => x.Technologies).HasMaxLength(1000);
            entity.Property(x => x.ProjectStatus).HasMaxLength(30).IsRequired();
            entity.Property(x => x.Keywords).HasMaxLength(1000);
            entity.Property(x => x.ExclusionReason).HasMaxLength(1000);

            entity.HasOne(x => x.CreatedByUser)
                .WithMany()
                .HasForeignKey(x => x.CreatedByUserId)
                .OnDelete(DeleteBehavior.SetNull);

            entity.HasIndex(x => x.IsActive);
            entity.HasIndex(x => x.Major);
            entity.HasIndex(x => x.Domain);
            entity.HasIndex(x => x.CompletionYear);
            entity.HasIndex(x => x.ExcludeSimilarIdeas);
        });

        modelBuilder.Entity<HistoricalFypFutureOpportunity>(entity =>
        {
            entity.ToTable("historical_fyp_future_opportunities");
            entity.HasKey(x => x.Id);

            entity.Property(x => x.Title).HasMaxLength(200).IsRequired();
            entity.Property(x => x.Description).HasMaxLength(4000).IsRequired();
            entity.Property(x => x.SuggestedDomain).HasMaxLength(100);
            entity.Property(x => x.SuggestedTechnologies).HasMaxLength(1000);
            entity.Property(x => x.ResearchGap).HasMaxLength(2000);

            entity.HasOne(x => x.HistoricalFypProject)
                .WithMany(x => x.FutureOpportunities)
                .HasForeignKey(x => x.HistoricalFypProjectId)
                // Deliberately Restrict, not Cascade, even though this is an
                // owned-child relationship: an accidental HistoricalFypProject
                // deletion must never silently erase the future-opportunity
                // history attached to it. The admin UI only ever
                // activates/deactivates projects, never hard-deletes one
                // with children still attached.
                .OnDelete(DeleteBehavior.Restrict);

            entity.HasOne(x => x.CreatedByUser)
                .WithMany()
                .HasForeignKey(x => x.CreatedByUserId)
                .OnDelete(DeleteBehavior.SetNull);

            entity.HasIndex(x => x.IsActive);
            entity.HasIndex(x => x.HistoricalFypProjectId);
            entity.HasIndex(x => x.Priority);
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

        modelBuilder.Entity<Notification>(entity =>
        {
            entity.ToTable("notifications");

            entity.HasKey(notification => notification.Id);

            entity.Property(notification => notification.Title)
                .HasMaxLength(200)
                .IsRequired();

            entity.Property(notification => notification.Message)
                .HasMaxLength(1200)
                .IsRequired();

            entity.Property(notification => notification.Type)
                .HasMaxLength(80)
                .HasDefaultValue("general")
                .IsRequired();

            entity.Property(notification => notification.Url)
                .HasMaxLength(500);

            entity.HasOne(notification => notification.RecipientUser)
                .WithMany()
                .HasForeignKey(notification => notification.RecipientUserId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasOne(notification => notification.Project)
                .WithMany()
                .HasForeignKey(notification => notification.ProjectId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasOne(notification => notification.ActorUser)
                .WithMany()
                .HasForeignKey(notification => notification.ActorUserId)
                .OnDelete(DeleteBehavior.SetNull);

            entity.HasIndex(notification => new
            {
                notification.RecipientUserId,
                notification.IsRead,
                notification.CreatedAt
            });

            entity.HasIndex(notification => new
            {
                notification.RecipientUserId,
                notification.CreatedAt
            });

            entity.HasIndex(notification => notification.ProjectId);
            entity.HasIndex(notification => notification.ActorUserId);
        });

        modelBuilder.Entity<AiOutputReview>(entity =>
        {
            // Idempotency guard for IdeaComparisonJobFinalizer (and any
            // other finalizer that persists AiOutputReview as its job's
            // parent output row) -- partial so legacy rows with JobId=null
            // are unaffected.
            entity.HasIndex(r => r.JobId)
                .IsUnique()
                .HasFilter("\"job_id\" IS NOT NULL");
        });

        modelBuilder.Entity<SupervisorRegistrationRequest>(entity =>
        {
            entity.ToTable("supervisor_registration_requests");

            entity.HasKey(request => request.Id);

            entity.Property(request => request.Id)
                .HasColumnName("id");

            entity.Property(request => request.FullName)
                .HasColumnName("full_name")
                .HasMaxLength(200)
                .IsRequired();

            entity.Property(request => request.Email)
                .HasColumnName("email")
                .HasMaxLength(256)
                .IsRequired();

            entity.Property(request => request.PasswordHash)
                .HasColumnName("password_hash")
                .HasMaxLength(200);

            entity.Property(request => request.AcademicTitle)
                .HasColumnName("academic_title")
                .HasMaxLength(100);

            entity.Property(request => request.University)
                .HasColumnName("university")
                .HasMaxLength(150);

            entity.Property(request => request.Department)
                .HasColumnName("department")
                .HasMaxLength(150);

            entity.Property(request => request.Specialization)
                .HasColumnName("specialization")
                .HasMaxLength(200);

            entity.Property(request => request.ProfessionalProfileUrl)
                .HasColumnName("professional_profile_url")
                .HasMaxLength(300);

            entity.Property(request => request.VerificationCodeHash)
                .HasColumnName("verification_code_hash")
                .HasMaxLength(100);

            entity.Property(request => request.VerificationSentAtUtc)
                .HasColumnName("verification_sent_at_utc");

            entity.Property(request => request.VerificationExpiresAtUtc)
                .HasColumnName("verification_expires_at_utc");

            entity.Property(request => request.VerificationFailedAttemptCount)
                .HasColumnName("verification_failed_attempt_count")
                .HasDefaultValue(0);

            entity.Property(request => request.Status)
                .HasColumnName("status")
                .HasMaxLength(30)
                .HasDefaultValue(SupervisorRegistrationStatus.PendingEmail);

            entity.Property(request => request.CreatedAtUtc)
                .HasColumnName("created_at_utc");

            entity.Property(request => request.VerifiedAtUtc)
                .HasColumnName("verified_at_utc");

            entity.Property(request => request.SubmittedAtUtc)
                .HasColumnName("submitted_at_utc");

            entity.Property(request => request.ReviewedAtUtc)
                .HasColumnName("reviewed_at_utc");

            entity.Property(request => request.ReviewedByAdminId)
                .HasColumnName("reviewed_by_admin_id");

            entity.Property(request => request.RejectionReason)
                .HasColumnName("rejection_reason")
                .HasMaxLength(500);

            entity.HasOne(request => request.ReviewedByAdmin)
                .WithMany()
                .HasForeignKey(request => request.ReviewedByAdminId)
                // Keep the application row and clear only the
                // reviewing administrator reference if that admin
                // account is later deleted.
                .OnDelete(DeleteBehavior.SetNull);

            entity.HasIndex(request => request.Status);
            entity.HasIndex(request => request.CreatedAtUtc);
            entity.HasIndex(request => request.ReviewedByAdminId);

            /*
             * PostgreSQL partial unique index: at most one active
             * application (not yet approved or rejected) can exist
             * per email address. Approved/rejected rows remain as
             * audit history and do not block a future reapplication.
             */
            entity.HasIndex(request => request.Email)
                .IsUnique()
                .HasFilter(
                    "\"status\" IN "
                    + "('pending_email', 'awaiting_details', 'pending_admin')");
        });

        modelBuilder.Entity<AiAgentJob>(entity =>
        {
            entity.HasIndex(j => j.JobId).IsUnique();

            // Supports the §7 relevance-scoped "current job" lookup
            // (FindJobByHashAsync / FindActiveJobAsync).
            entity.HasIndex(j => new { j.UserId, j.ProjectId, j.AgentName, j.RequestHash, j.UpdatedAtUtc });

            entity.HasOne(j => j.User)
                .WithMany()
                .HasForeignKey(j => j.UserId)
                .OnDelete(DeleteBehavior.Cascade);

            entity.HasOne(j => j.Project)
                .WithMany()
                .HasForeignKey(j => j.ProjectId)
                .OnDelete(DeleteBehavior.SetNull);

            // Postgres's built-in xmin system column as the optimistic
            // concurrency token -- no explicit version column needed.
            entity.Property<uint>("xmin").IsRowVersion();

            // The true duplicate-job/one-active-job-per-request guard is a
            // partial unique index over COALESCE(project_id, -1) added via
            // raw SQL in the AddAiAgentJob migration (EF's fluent HasIndex
            // can't express a COALESCE expression over a nullable column).
        });

        // SupervisorPreferenceBatch, SupervisorPreference, and
        // GoogleCalendarToken continue to use their existing convention-based
        // mappings. Notification now has explicit relationships and indexes.
        modelBuilder.Entity<ProjectDnaAnalysisRecord>(
    entity =>
    {
        entity.ToTable(
            "project_dna_analyses");

        entity.HasKey(record =>
            record.Id);

        entity.Property(record =>
                record.AnalysisJson)
            .HasColumnType("text")
            .IsRequired();

        entity.Property(record =>
                record.Source)
            .HasMaxLength(120);

        entity.Property(record =>
                record.Provider)
            .HasMaxLength(120);

        entity.Property(record =>
                record.ModelUsed)
            .HasMaxLength(200);

        entity.HasOne(record =>
                record.Project)
            .WithMany()
            .HasForeignKey(record =>
                record.ProjectId)
            .OnDelete(
                DeleteBehavior.Cascade);

        entity.HasOne(record =>
                record.ProjectIdea)
            .WithMany()
            .HasForeignKey(record =>
                record.ProjectIdeaId)
            .OnDelete(
                DeleteBehavior.Cascade);

        entity.HasOne(record =>
                record.GeneratedByUser)
            .WithMany()
            .HasForeignKey(record =>
                record.GeneratedByUserId)
            .OnDelete(
                DeleteBehavior.Restrict);

        entity.HasIndex(record => new
        {
            record.ProjectId,
            record.ProjectIdeaId,
            record.GeneratedAtUtc
        });
    });
    }
}