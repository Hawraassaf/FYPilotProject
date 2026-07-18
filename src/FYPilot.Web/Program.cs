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

// SEC-0 fail-fast check: crash at STARTUP with a clear message if the AI
// service key is missing, rather than discovering it later via mysterious
// 401s on every AI call. AiServiceClient's own constructor also reads this
// env var independently (see AiServiceClient.cs) — this call exists purely
// for the fail-fast validation, its return value is intentionally unused.
static string GetRequiredAiServiceApiKey(IConfiguration configuration)
{
    var key =
        Environment.GetEnvironmentVariable("AI_SERVICE_API_KEY")
        ?? configuration["AiService:InternalApiKey"]
        ?? configuration["AiService:ApiKey"];

    if (string.IsNullOrWhiteSpace(key))
    {
        throw new InvalidOperationException(
            "AI_SERVICE_API_KEY is missing. Set the same key in both the .NET app and the FastAPI service."
        );
    }

    return key;
}

_ = GetRequiredAiServiceApiKey(builder.Configuration);

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
// NOTE: the X-Internal-Api-Key header is added by AiServiceClient itself
// (in its own constructor), not via IHttpClientFactory's default client —
// AiServiceClient builds its own HttpClient internally. An earlier version
// of this file registered the header on Options.DefaultName here, but
// nothing consumed that client for AI calls, so it did nothing. Removed.
builder.Services.AddSingleton<IAiServiceClient, AiServiceClient>();
builder.Services.AddHttpClient();

// ── Documentation Generator Service ───────────────────────────────────────────
builder.Services.AddScoped<
    IDocumentationGeneratorService,
    DocumentationGeneratorService>();

// ── Session: used to store generated AI ideas for Shuffle 2-2-2 ───────────────
// (FIX: this was registered twice — once configured, once bare immediately
// after the Google Calendar block below. The bare call would have silently
// overridden these options with defaults. Kept only this configured one.)
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
