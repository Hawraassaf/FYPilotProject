using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using FYPilot.Infrastructure.Services.Finalizers;
using FYPilot.Web.Endpoints;
using FYPilot.Web.Configuration;
using FYPilot.Web.Hubs;
using FYPilot.Web.Middleware;
using FYPilot.Web.Services.AiAgentJobs;
using FYPilot.Web.Services.GoogleCalendar;
using FYPilot.Web.Services.Meetings;
using FYPilot.Web.Services.Notifications;
using FYPilot.Web.Services.Supervisors;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

// ── Database ──────────────────────────────────────────────────────────────────
static string BuildConnectionString()
{
    var url = Environment.GetEnvironmentVariable("DATABASE_URL");

    if (!string.IsNullOrWhiteSpace(url))
    {
        return url;
    }

    var host =
        Environment.GetEnvironmentVariable("PGHOST")
        ?? "localhost";

    var port =
        Environment.GetEnvironmentVariable("PGPORT")
        ?? "5432";

    var database =
        Environment.GetEnvironmentVariable("PGDATABASE")
        ?? "fyp_db";

    var user =
        Environment.GetEnvironmentVariable("PGUSER")
        ?? "postgres";

    var password =
        Environment.GetEnvironmentVariable("PGPASSWORD")
        ?? "123456";

    return
        $"Host={host};" +
        $"Port={port};" +
        $"Database={database};" +
        $"Username={user};" +
        $"Password={password};" +
        "SSL Mode=Disable;" +
        "Trust Server Certificate=true;";
}

// ── AI Service Key ────────────────────────────────────────────────────────────
static string GetRequiredAiServiceApiKey(
    IConfiguration configuration)
{
    var key =
        Environment.GetEnvironmentVariable("AI_SERVICE_API_KEY")
        ?? configuration["AiService:InternalApiKey"]
        ?? configuration["AiService:ApiKey"];

    if (string.IsNullOrWhiteSpace(key))
    {
        throw new InvalidOperationException(
            "AI_SERVICE_API_KEY is missing. " +
            "Set the same key in both the .NET app " +
            "and the FastAPI service.");
    }

    return key;
}

_ = GetRequiredAiServiceApiKey(builder.Configuration);

// ── Database Context ──────────────────────────────────────────────────────────
builder.Services.AddDbContext<ApplicationDbContext>(
    options =>
        options.UseNpgsql(BuildConnectionString()));

// ── Cookie Authentication ─────────────────────────────────────────────────────
builder.Services
    .AddAuthentication(
        CookieAuthenticationDefaults.AuthenticationScheme)
    .AddCookie(options =>
    {
        options.LoginPath = "/Account/Login";
        options.LogoutPath = "/Account/Logout";
        options.AccessDeniedPath = "/Account/Login";

        options.ExpireTimeSpan = TimeSpan.FromHours(8);
        options.SlidingExpiration = true;

        options.Cookie.HttpOnly = true;
        options.Cookie.SameSite = SameSiteMode.Lax;
        options.Cookie.IsEssential = true;
    });

// ── Authorization ─────────────────────────────────────────────────────────────
builder.Services.AddAuthorization(options =>
{
    options.AddPolicy(
        "StudentOnly",
        policy => policy.RequireRole("student"));

    options.AddPolicy(
        "SupervisorOnly",
        policy => policy.RequireRole("supervisor"));

    options.AddPolicy(
        "AdminOnly",
        policy => policy.RequireRole("admin"));
});

// ── AI Service ────────────────────────────────────────────────────────────────
builder.Services.AddSingleton<
    IAiServiceClient,
    AiServiceClient>();

// Separate, short-timeout client for Python's centralized job endpoints --
// only ever called by AiAgentJobCoordinator's fast poll loop, never from a
// browser-facing handler (see AiJobsPythonClient's doc comment).
builder.Services.AddSingleton<
    IAiJobsPythonClient,
    AiJobsPythonClient>();

builder.Services.AddHttpClient();

// ── Documentation Generator Service ───────────────────────────────────────────
builder.Services.AddScoped<
    IDocumentationGeneratorService,
    DocumentationGeneratorService>();

// ── Session ───────────────────────────────────────────────────────────────────
builder.Services.AddDistributedMemoryCache();

builder.Services.AddSession(options =>
{
    options.IdleTimeout = TimeSpan.FromMinutes(30);
    options.Cookie.HttpOnly = true;
    options.Cookie.IsEssential = true;
    options.Cookie.SameSite = SameSiteMode.Lax;
});

// ── Razor Pages + SignalR ─────────────────────────────────────────────────────
builder.Services.AddRazorPages();
builder.Services.AddSignalR();
builder.Services.AddAntiforgery();
builder.Services.AddHealthChecks();

// ── Google Calendar ───────────────────────────────────────────────────────────
builder.Services.Configure<GoogleCalendarSettings>(
    builder.Configuration.GetSection("GoogleCalendar"));

builder.Services.AddScoped<
    IGoogleCalendarService,
    GoogleCalendarService>();

// ── Email Sender ──────────────────────────────────────────────────────────────
builder.Services.Configure<SmtpSettings>(
    builder.Configuration.GetSection("Smtp"));

builder.Services.AddScoped<
    IEmailSender,
    SmtpEmailSender>();

// ── Notifications + Supervisor Services ───────────────────────────────────────
builder.Services.AddScoped<
    INotificationService,
    NotificationService>();

builder.Services.AddScoped<SupervisorAccessService>();

// ── Shared Project Context ────────────────────────────────────────────────────
builder.Services.AddScoped<
    IProjectAccessService,
    ProjectAccessService>();

builder.Services.AddScoped<
    IActiveProjectService,
    ActiveProjectService>();

// ── Idea Generation Knowledge Base ────────────────────────────────────────────
builder.Services.AddScoped<
    IAdminIdeaContextService,
    AdminIdeaContextService>();

// ── Centralized AI Agent Loading System ───────────────────────────────────────
builder.Services.AddScoped<
    IAiAgentJobService,
    AiAgentJobService>();

// One IAiAgentJobFinalizer per agent, keyed by AgentName -- resolved by
// AiAgentJobCoordinator via GetRequiredKeyedService<IAiAgentJobFinalizer>(job.AgentName).
builder.Services.AddKeyedScoped<
    IAiAgentJobFinalizer,
    IdeaComparisonJobFinalizer>("IdeaComparisonAgent");

// In-process broadcast pub/sub for live job events (per-subscriber
// channels -- see AiAgentJobEventBus) shared between AiAgentJobCoordinator
// (publisher) and the SSE endpoint (subscriber, added in a later phase).
builder.Services.AddSingleton<
    IAiAgentJobEventBus,
    AiAgentJobEventBus>();

// Drives every AiAgentJob from "Python's worker finished" through
// "persisted and marked complete" independently of any connected browser.
builder.Services.AddHostedService<AiAgentJobCoordinator>();

// ── Background Workers ────────────────────────────────────────────────────────
builder.Services.AddHostedService<MeetingReminderWorker>();

var app = builder.Build();

// ── Error Handling ────────────────────────────────────────────────────────────
if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Error");
    app.UseHsts();
}

// ── Middleware Pipeline ───────────────────────────────────────────────────────
app.UseStaticFiles();

app.UseRouting();

app.UseSession();

app.UseAuthentication();

/*
 * Blocks a newly created administrator from opening any other page
 * until the temporary password has been changed.
 *
 * It must remain after UseAuthentication because it needs to know
 * which user is currently logged in.
 */
app.UseMiddleware<ForcePasswordChangeMiddleware>();

app.UseAuthorization();

// ── SignalR Hubs ──────────────────────────────────────────────────────────────
app.MapHub<FeedbackChatHub>(
    "/hubs/feedback-chat");

app.MapHub<NotificationHub>(
    "/hubs/notifications");

// ── Endpoints ─────────────────────────────────────────────────────────────────
app.MapHealthChecks("/healthz");
app.MapHub<ProjectDiscussionHub>(
    "/hubs/project-discussion");

// Centralized AI Agent Loading System -- shared browser-facing surface
// (current/snapshot/events/result/cancel) reused by every wired agent.
app.MapAiAgentJobEndpoints();

app.MapRazorPages();
app.MapRazorPages();

// ── Database Startup ──────────────────────────────────────────────────────────
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider
        .GetRequiredService<ApplicationDbContext>();

    try
    {
        if (!db.Database.CanConnect())
        {
            throw new InvalidOperationException(
                "The application could not connect to the database.");
        }

        app.Logger.LogInformation("Database connection ready.");
    }
    catch (Exception ex)
    {
        app.Logger.LogError(
            ex,
            "Database startup error — continuing anyway.");
    }
}

// ── Application URL ───────────────────────────────────────────────────────────
var applicationPort =
    Environment.GetEnvironmentVariable("PORT")
    ?? "5000";

app.Run(
    $"http://0.0.0.0:{applicationPort}");