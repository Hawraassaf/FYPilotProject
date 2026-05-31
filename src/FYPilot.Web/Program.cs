using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

// ── Database ──────────────────────────────────────────────────────────────────
static string BuildConnectionString()
{
    // Priority 1: full DATABASE_URL
    var url = Environment.GetEnvironmentVariable("DATABASE_URL");
    if (!string.IsNullOrWhiteSpace(url)) return url;

    // Priority 2: individual PG* variables, with local-dev defaults
    var host = Environment.GetEnvironmentVariable("PGHOST") ?? "localhost";
    var port = Environment.GetEnvironmentVariable("PGPORT") ?? "5432";
    var db = Environment.GetEnvironmentVariable("PGDATABASE") ?? "fyp_db";
    var user = Environment.GetEnvironmentVariable("PGUSER") ?? "postgres";
    var pass = Environment.GetEnvironmentVariable("PGPASSWORD") ?? "123456";
    return $"Host={host};Port={port};Database={db};Username={user};Password={pass};SSL Mode=Disable;Trust Server Certificate=true;";
}

builder.Services.AddDbContext<ApplicationDbContext>(options =>
    options.UseNpgsql(BuildConnectionString()));

// ── Cookie Authentication ─────────────────────────────────────────────────────
builder.Services.AddAuthentication(CookieAuthenticationDefaults.AuthenticationScheme)
    .AddCookie(options =>
    {
        options.LoginPath = "/Account/Login";
        options.LogoutPath = "/Account/Logout";
        options.AccessDeniedPath = "/Account/Login";
        options.ExpireTimeSpan = TimeSpan.FromHours(8);
        options.SlidingExpiration = true;
        options.Cookie.HttpOnly = true;
        options.Cookie.SameSite = SameSiteMode.Lax;
    });

// ── Authorization ─────────────────────────────────────────────────────────────
builder.Services.AddAuthorization(options =>
{
    options.AddPolicy("StudentOnly", p => p.RequireRole("student"));
    options.AddPolicy("SupervisorOnly", p => p.RequireRole("supervisor"));
    options.AddPolicy("AdminOnly", p => p.RequireRole("admin"));
});

// ── AI Service ────────────────────────────────────────────────────────────────
builder.Services.AddSingleton<IAiServiceClient, AiServiceClient>();
builder.Services.AddHttpClient();

// ── Documentation Generator Service ───────────────────────────────────────────
builder.Services.AddScoped<IDocumentationGeneratorService, DocumentationGeneratorService>();

// ── Session: used to store generated AI ideas for Shuffle 2-2-2 ───────────────
builder.Services.AddDistributedMemoryCache();

builder.Services.AddSession(options =>
{
    options.IdleTimeout = TimeSpan.FromMinutes(30);
    options.Cookie.HttpOnly = true;
    options.Cookie.IsEssential = true;
});

// ── Razor Pages ───────────────────────────────────────────────────────────────
builder.Services.AddRazorPages();
builder.Services.AddAntiforgery();
builder.Services.AddHealthChecks();

var app = builder.Build();

if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Error");
    app.UseHsts();
}

app.UseStaticFiles();
app.UseRouting();

app.UseSession();

app.UseAuthentication();
app.UseAuthorization();

app.MapHealthChecks("/healthz");
app.MapRazorPages();

// ── Database: EnsureCreated + Seed ────────────────────────────────────────────
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
    try
    {
        db.Database.EnsureCreated();
        await DataSeeder.SeedAsync(db);
        app.Logger.LogInformation("Database ready and seeded");
    }
    catch (Exception ex)
    {
        app.Logger.LogError(ex, "Database startup error — continuing anyway");
    }
}

var port = Environment.GetEnvironmentVariable("PORT") ?? "5000";
app.Run($"http://0.0.0.0:{port}");