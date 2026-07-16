using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using FYPilot.Web.Configuration;
using FYPilot.Web.Hubs;
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

    var host = Environment.GetEnvironmentVariable("PGHOST") ?? "localhost";
    var port = Environment.GetEnvironmentVariable("PGPORT") ?? "5432";
    var db = Environment.GetEnvironmentVariable("PGDATABASE") ?? "fyp_db";
    var user = Environment.GetEnvironmentVariable("PGUSER") ?? "postgres";
    var pass = Environment.GetEnvironmentVariable("PGPASSWORD") ?? "123456";

    return
        $"Host={host};Port={port};Database={db};Username={user};Password={pass};SSL Mode=Disable;Trust Server Certificate=true;";
}

builder.Services.AddDbContext<ApplicationDbContext>(options =>
    options.UseNpgsql(BuildConnectionString()));

// ── Cookie Authentication ─────────────────────────────────────────────────────
builder.Services
    .AddAuthentication(CookieAuthenticationDefaults.AuthenticationScheme)
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
builder.Services.AddSingleton<IAiServiceClient, AiServiceClient>();
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
builder.Services.AddSession();
// ── Email Sender ──────────────────────────────────────────────────────────────
builder.Services.Configure<SmtpSettings>(
    builder.Configuration.GetSection("Smtp"));

builder.Services.AddScoped<IEmailSender, SmtpEmailSender>();

// ── Notifications + Supervisor Services ───────────────────────────────────────
builder.Services.AddScoped<
    INotificationService,
    NotificationService>();

builder.Services.AddScoped<SupervisorAccessService>();

// ── Background Workers ────────────────────────────────────────────────────────
builder.Services.AddHostedService<MeetingReminderWorker>();

var app = builder.Build();

// ── Error Handling ────────────────────────────────────────────────────────────
if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Error");
    app.UseHsts();
}

// ── Middleware ────────────────────────────────────────────────────────────────
app.UseStaticFiles();

app.UseRouting();

app.UseSession();

app.UseAuthentication();
app.UseAuthorization();

// ── SignalR Hubs ──────────────────────────────────────────────────────────────
// Must be mapped after authentication and authorization middleware.
app.MapHub<FeedbackChatHub>("/hubs/feedback-chat");
app.MapHub<NotificationHub>("/hubs/notifications");

// ── Endpoints ─────────────────────────────────────────────────────────────────
app.MapHealthChecks("/healthz");
app.MapRazorPages();

// ── Database Startup ──────────────────────────────────────────────────────────
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider
        .GetRequiredService<ApplicationDbContext>();

    try
    {
        db.Database.EnsureCreated();

        // Keep this OFF to avoid inserting old demo data again.
        // await DataSeeder.SeedAsync(db);

        app.Logger.LogInformation("Database ready.");
    }
    catch (Exception ex)
    {
        app.Logger.LogError(
            ex,
            "Database startup error — continuing anyway.");
    }
}

// ── Application URL ───────────────────────────────────────────────────────────
var port = Environment.GetEnvironmentVariable("PORT") ?? "5000";

app.Run($"http://0.0.0.0:{port}");