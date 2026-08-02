using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddAiAgentJob : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "ai_agent_jobs",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    job_id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<int>(type: "integer", nullable: false),
                    project_id = table.Column<int>(type: "integer", nullable: true),
                    agent_name = table.Column<string>(type: "character varying(80)", maxLength: 80, nullable: false),
                    request_hash = table.Column<string>(type: "character varying(64)", maxLength: 64, nullable: false),
                    request_json = table.Column<string>(type: "text", nullable: false),
                    status = table.Column<string>(type: "character varying(20)", maxLength: 20, nullable: false),
                    stage_key = table.Column<string>(type: "character varying(60)", maxLength: 60, nullable: false),
                    stage_states_json = table.Column<string>(type: "text", nullable: false),
                    message = table.Column<string>(type: "character varying(300)", maxLength: 300, nullable: false),
                    provider = table.Column<string>(type: "character varying(60)", maxLength: 60, nullable: true),
                    model = table.Column<string>(type: "character varying(120)", maxLength: 120, nullable: true),
                    provider_attempt_number = table.Column<int>(type: "integer", nullable: false),
                    current_attempt_chunk_count = table.Column<int>(type: "integer", nullable: false),
                    current_attempt_token_count = table.Column<int>(type: "integer", nullable: true),
                    provider_attempts_json = table.Column<string>(type: "text", nullable: false),
                    last_event_sequence = table.Column<long>(type: "bigint", nullable: false),
                    result_json = table.Column<string>(type: "text", nullable: true),
                    result_persisted_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    python_accepted_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    coordinator_owner_id = table.Column<Guid>(type: "uuid", nullable: true),
                    coordinator_lease_expires_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    error_code = table.Column<string>(type: "character varying(60)", maxLength: 60, nullable: true),
                    error_message = table.Column<string>(type: "character varying(500)", maxLength: 500, nullable: true),
                    created_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    started_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    updated_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    completed_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    xmin = table.Column<uint>(type: "xid", rowVersion: true, nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_ai_agent_jobs", x => x.id);
                    table.ForeignKey(
                        name: "FK_ai_agent_jobs_projects_project_id",
                        column: x => x.project_id,
                        principalTable: "projects",
                        principalColumn: "id",
                        onDelete: ReferentialAction.SetNull);
                    table.ForeignKey(
                        name: "FK_ai_agent_jobs_users_user_id",
                        column: x => x.user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "IX_ai_agent_jobs_job_id",
                table: "ai_agent_jobs",
                column: "job_id",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_ai_agent_jobs_project_id",
                table: "ai_agent_jobs",
                column: "project_id");

            migrationBuilder.CreateIndex(
                name: "IX_ai_agent_jobs_user_id_project_id_agent_name_request_hash_up~",
                table: "ai_agent_jobs",
                columns: new[] { "user_id", "project_id", "agent_name", "request_hash", "updated_at_utc" });

            // The real duplicate-job / one-active-job-per-request guard.
            // EF's fluent HasIndex API can't express a COALESCE expression
            // over the nullable project_id column, so this is raw SQL.
            // COALESCE(project_id, -1) treats "no project" (e.g. Mentor
            // Chat) as a single comparable value -- Postgres unique indexes
            // otherwise treat every NULL as distinct, which would silently
            // defeat uniqueness for project-less agents.
            migrationBuilder.Sql("""
                CREATE UNIQUE INDEX ux_ai_agent_jobs_active
                ON ai_agent_jobs (user_id, COALESCE(project_id, -1), agent_name, request_hash)
                WHERE status IN ('queued','running','cancel_requested','awaiting_finalize','finalizing');
                """);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.Sql("DROP INDEX IF EXISTS ux_ai_agent_jobs_active;");

            migrationBuilder.DropTable(
                name: "ai_agent_jobs");
        }
    }
}
