using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddAiOutputReviews : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "ai_output_reviews",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    review_run_id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<int>(type: "integer", nullable: false),
                    project_idea_id = table.Column<int>(type: "integer", nullable: true),
                    mentor_chat_session_id = table.Column<int>(type: "integer", nullable: true),
                    agent_name = table.Column<string>(type: "character varying(80)", maxLength: 80, nullable: false),
                    status = table.Column<string>(type: "character varying(40)", maxLength: 40, nullable: false),
                    usable = table.Column<bool>(type: "boolean", nullable: false),
                    was_rewritten = table.Column<bool>(type: "boolean", nullable: false),
                    attempts = table.Column<int>(type: "integer", nullable: false),
                    quality_score = table.Column<int>(type: "integer", nullable: true),
                    decision_reason = table.Column<string>(type: "text", nullable: false),
                    generator_provider = table.Column<string>(type: "character varying(60)", maxLength: 60, nullable: true),
                    generator_model = table.Column<string>(type: "character varying(120)", maxLength: 120, nullable: true),
                    reviewer_provider = table.Column<string>(type: "character varying(60)", maxLength: 60, nullable: true),
                    reviewer_model = table.Column<string>(type: "character varying(120)", maxLength: 120, nullable: true),
                    firewall_status = table.Column<string>(type: "character varying(40)", maxLength: 40, nullable: false),
                    firewall_input_flags_json = table.Column<string>(type: "text", nullable: false),
                    firewall_output_flags_json = table.Column<string>(type: "text", nullable: false),
                    issues_json = table.Column<string>(type: "text", nullable: false),
                    strengths_json = table.Column<string>(type: "text", nullable: false),
                    attempt_history_json = table.Column<string>(type: "text", nullable: false),
                    reviewer_version = table.Column<string>(type: "character varying(40)", maxLength: 40, nullable: false),
                    created_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    completed_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_ai_output_reviews", x => x.id);
                    table.ForeignKey(
                        name: "FK_ai_output_reviews_mentor_chat_sessions_mentor_chat_session_~",
                        column: x => x.mentor_chat_session_id,
                        principalTable: "mentor_chat_sessions",
                        principalColumn: "id");
                    table.ForeignKey(
                        name: "FK_ai_output_reviews_users_user_id",
                        column: x => x.user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "IX_ai_output_reviews_mentor_chat_session_id",
                table: "ai_output_reviews",
                column: "mentor_chat_session_id");

            migrationBuilder.CreateIndex(
                name: "IX_ai_output_reviews_user_id",
                table: "ai_output_reviews",
                column: "user_id");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "ai_output_reviews");
        }
    }
}
