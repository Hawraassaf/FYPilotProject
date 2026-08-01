using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations;

[DbContext(typeof(ApplicationDbContext))]
[Migration("20260801163000_AllowMultipleSupervisorEvaluations")]
public partial class AllowMultipleSupervisorEvaluations
    : Migration
{
    protected override void Up(
        MigrationBuilder migrationBuilder)
    {
        /*
         * The real PostgreSQL table uses snake_case:
         * supervisor_evaluations
         *
         * The previous unique index allowed only one
         * evaluation for a project and idea.
         */
        migrationBuilder.Sql(
            """
            DROP INDEX IF EXISTS
                "IX_supervisor_evaluations_project_id_idea_id";
            """);

        /*
         * Recreate the same lookup as a normal, non-unique
         * index so multiple evaluation rounds are allowed.
         */
        migrationBuilder.Sql(
            """
            CREATE INDEX IF NOT EXISTS
                "IX_supervisor_evaluations_project_id_idea_id"
            ON "supervisor_evaluations"
            (
                "project_id",
                "idea_id"
            );
            """);

        migrationBuilder.Sql(
            """
            CREATE INDEX IF NOT EXISTS
                "IX_supervisor_evaluations_history_lookup"
            ON "supervisor_evaluations"
            (
                "project_id",
                "idea_id",
                "supervisor_id",
                "updated_at" DESC,
                "id" DESC
            );
            """);
    }

    protected override void Down(
        MigrationBuilder migrationBuilder)
    {
        migrationBuilder.Sql(
            """
            DROP INDEX IF EXISTS
                "IX_supervisor_evaluations_history_lookup";
            """);

        migrationBuilder.Sql(
            """
            DROP INDEX IF EXISTS
                "IX_supervisor_evaluations_project_id_idea_id";
            """);

        /*
         * Restores the previous one-evaluation-only rule.
         * This rollback can fail after multiple evaluation
         * rounds have been created.
         */
        migrationBuilder.Sql(
            """
            CREATE UNIQUE INDEX
                "IX_supervisor_evaluations_project_id_idea_id"
            ON "supervisor_evaluations"
            (
                "project_id",
                "idea_id"
            )
            WHERE "project_id" IS NOT NULL;
            """);
    }
}