using System.Text;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;
using Microsoft.OpenApi.Models;

var builder = WebApplication.CreateBuilder(args);

// ── Database ─────────────────────────────────────────────────────────────────
static string BuildConnectionString()
{
    // Priority 1: full DATABASE_URL
    var url = Environment.GetEnvironmentVariable("DATABASE_URL");
    if (!string.IsNullOrWhiteSpace(url)) return url;

    // Priority 2: individual PG* variables, with local-dev defaults
    var host = Environment.GetEnvironmentVariable("PGHOST")     ?? "localhost";
    var port = Environment.GetEnvironmentVariable("PGPORT")     ?? "5432";
    var db   = Environment.GetEnvironmentVariable("PGDATABASE") ?? "fyp_db";
    var user = Environment.GetEnvironmentVariable("PGUSER")     ?? "postgres";
    var pass = Environment.GetEnvironmentVariable("PGPASSWORD") ?? "123456";
    return $"Host={host};Port={port};Database={db};Username={user};Password={pass};SSL Mode=Disable;Trust Server Certificate=true;";
}

builder.Services.AddDbContext<ApplicationDbContext>(options =>
    options.UseNpgsql(BuildConnectionString()));

// ── JWT Auth ─────────────────────────────────────────────────────────────────
var jwtSecret = builder.Configuration["Jwt:Secret"]
    ?? Environment.GetEnvironmentVariable("JWT_SECRET")
    ?? "fallback_dev_secret_change_in_production_min32chars!!";

builder.Services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
    .AddJwtBearer(options =>
    {
        options.TokenValidationParameters = new TokenValidationParameters
        {
            ValidateIssuer            = true,
            ValidateAudience          = true,
            ValidateLifetime          = true,
            ValidateIssuerSigningKey  = true,
            ValidIssuer               = "fypilot",
            ValidAudience             = "fypilot",
            IssuerSigningKey          = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(jwtSecret)),
        };
    });

builder.Services.AddAuthorization();

// ── Application Services (Clean Architecture DI) ─────────────────────────────
builder.Services.AddScoped<ITokenService, TokenService>();
builder.Services.AddSingleton<IAiServiceClient, AiServiceClient>();
builder.Services.AddSingleton<DataScienceService>();
builder.Services.AddHttpClient();
builder.Services.AddControllers();

// ── Swagger ───────────────────────────────────────────────────────────────────
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen(c =>
{
    c.SwaggerDoc("v1", new OpenApiInfo
    {
        Title       = "FYPilot API",
        Version     = "v1",
        Description = "FYPilot — AI-Powered FYP Planning Platform (Clean Architecture)"
    });
    c.AddSecurityDefinition("Bearer", new OpenApiSecurityScheme
    {
        Description = "JWT Authorization header. Enter: Bearer {token}",
        Name        = "Authorization",
        In          = ParameterLocation.Header,
        Type        = SecuritySchemeType.ApiKey,
        Scheme      = "Bearer"
    });
    c.AddSecurityRequirement(new OpenApiSecurityRequirement
    {
        {
            new OpenApiSecurityScheme
            {
                Reference = new OpenApiReference { Type = ReferenceType.SecurityScheme, Id = "Bearer" }
            },
            Array.Empty<string>()
        }
    });
});

// ── CORS ──────────────────────────────────────────────────────────────────────
builder.Services.AddCors(options =>
    options.AddDefaultPolicy(p => p.AllowAnyOrigin().AllowAnyHeader().AllowAnyMethod()));

var app = builder.Build();

app.UseSwagger();
app.UseSwaggerUI(c => c.SwaggerEndpoint("/swagger/v1/swagger.json", "FYPilot API v1"));

app.UseCors();
app.UseAuthentication();
app.UseAuthorization();
app.MapControllers();

// Minimal health endpoint (no DB required)
app.MapGet("/api/healthz", () => Results.Ok(new
{
    status  = "ok",
    service = "FYPilot .NET API (Clean Architecture)",
    time    = DateTime.UtcNow
}));

// ── Database: EnsureCreated + Seed ────────────────────────────────────────────
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
    try
    {
        db.Database.EnsureCreated();
        await DataSeeder.SeedAsync(db);
    }
    catch (Exception ex)
    {
        var startupLogger = scope.ServiceProvider.GetRequiredService<ILogger<Program>>();
        startupLogger.LogError(ex, "Database startup error — continuing anyway");
    }
}

var port = Environment.GetEnvironmentVariable("PORT") ?? "8080";
app.Run($"http://0.0.0.0:{port}");
