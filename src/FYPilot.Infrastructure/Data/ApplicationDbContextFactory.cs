using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace FYPilot.Infrastructure.Data;

/// <summary>
/// Creates ApplicationDbContext for EF Core CLI commands
/// without starting the complete Web application.
/// </summary>
public sealed class ApplicationDbContextFactory
    : IDesignTimeDbContextFactory<ApplicationDbContext>
{
    public ApplicationDbContext CreateDbContext(
        string[] args)
    {
        var connectionString =
            BuildConnectionString();

        var optionsBuilder =
            new DbContextOptionsBuilder<
                ApplicationDbContext>();

        optionsBuilder.UseNpgsql(
            connectionString);

        return new ApplicationDbContext(
            optionsBuilder.Options);
    }

    private static string BuildConnectionString()
    {
        var databaseUrl =
            Environment.GetEnvironmentVariable(
                "DATABASE_URL");

        if (!string.IsNullOrWhiteSpace(
                databaseUrl))
        {
            return databaseUrl;
        }

        var host =
            Environment.GetEnvironmentVariable(
                "PGHOST") ?? "localhost";

        var port =
            Environment.GetEnvironmentVariable(
                "PGPORT") ?? "5432";

        var database =
            Environment.GetEnvironmentVariable(
                "PGDATABASE") ?? "fyp_db";

        var username =
            Environment.GetEnvironmentVariable(
                "PGUSER") ?? "postgres";

        var password =
            Environment.GetEnvironmentVariable(
                "PGPASSWORD");

        if (string.IsNullOrWhiteSpace(password))
        {
            throw new InvalidOperationException(
                "PGPASSWORD is missing. Set it before running EF Core commands.");
        }

        return
            $"Host={host};" +
            $"Port={port};" +
            $"Database={database};" +
            $"Username={username};" +
            $"Password={password};" +
            "SSL Mode=Disable;" +
            "Trust Server Certificate=true;";
    }
}