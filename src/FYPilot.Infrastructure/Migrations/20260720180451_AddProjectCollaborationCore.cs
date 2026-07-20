using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddProjectCollaborationCore : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<int>(
                name: "maximum_members",
                table: "projects",
                type: "integer",
                nullable: false,
                defaultValue: 3);

            migrationBuilder.AddColumn<int>(
                name: "project_idea_id",
                table: "projects",
                type: "integer",
                nullable: true);

            migrationBuilder.CreateTable(
                name: "project_members",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    project_id = table.Column<int>(type: "integer", nullable: false),
                    user_id = table.Column<int>(type: "integer", nullable: false),
                    role = table.Column<string>(type: "character varying(30)", maxLength: 30, nullable: false, defaultValue: "collaborator"),
                    status = table.Column<string>(type: "character varying(30)", maxLength: 30, nullable: false, defaultValue: "active"),
                    joined_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    left_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_project_members", x => x.id);
                    table.ForeignKey(
                        name: "FK_project_members_projects_project_id",
                        column: x => x.project_id,
                        principalTable: "projects",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_project_members_users_user_id",
                        column: x => x.user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateTable(
                name: "teammate_requests",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    project_id = table.Column<int>(type: "integer", nullable: false),
                    requested_by_user_id = table.Column<int>(type: "integer", nullable: false),
                    domain = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: false),
                    required_skills = table.Column<string>(type: "text", nullable: false),
                    student_message = table.Column<string>(type: "text", nullable: false),
                    requested_members_count = table.Column<int>(type: "integer", nullable: false, defaultValue: 1),
                    status = table.Column<string>(type: "character varying(30)", maxLength: 30, nullable: false, defaultValue: "pending"),
                    matched_user_id = table.Column<int>(type: "integer", nullable: true),
                    matched_by_supervisor_id = table.Column<int>(type: "integer", nullable: true),
                    created_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    updated_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    matched_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_teammate_requests", x => x.id);
                    table.ForeignKey(
                        name: "FK_teammate_requests_projects_project_id",
                        column: x => x.project_id,
                        principalTable: "projects",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_teammate_requests_users_matched_by_supervisor_id",
                        column: x => x.matched_by_supervisor_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.SetNull);
                    table.ForeignKey(
                        name: "FK_teammate_requests_users_matched_user_id",
                        column: x => x.matched_user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.SetNull);
                    table.ForeignKey(
                        name: "FK_teammate_requests_users_requested_by_user_id",
                        column: x => x.requested_by_user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateTable(
                name: "project_invitations",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    project_id = table.Column<int>(type: "integer", nullable: false),
                    invited_by_user_id = table.Column<int>(type: "integer", nullable: false),
                    invited_user_id = table.Column<int>(type: "integer", nullable: true),
                    invited_email = table.Column<string>(type: "character varying(256)", maxLength: 256, nullable: false),
                    token_hash = table.Column<string>(type: "character varying(64)", maxLength: 64, nullable: false),
                    status = table.Column<string>(type: "character varying(30)", maxLength: 30, nullable: false, defaultValue: "pending"),
                    source = table.Column<string>(type: "character varying(30)", maxLength: 30, nullable: false, defaultValue: "student_invite"),
                    teammate_request_id = table.Column<int>(type: "integer", nullable: true),
                    created_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    expires_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    responded_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_project_invitations", x => x.id);
                    table.ForeignKey(
                        name: "FK_project_invitations_projects_project_id",
                        column: x => x.project_id,
                        principalTable: "projects",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_project_invitations_teammate_requests_teammate_request_id",
                        column: x => x.teammate_request_id,
                        principalTable: "teammate_requests",
                        principalColumn: "id",
                        onDelete: ReferentialAction.SetNull);
                    table.ForeignKey(
                        name: "FK_project_invitations_users_invited_by_user_id",
                        column: x => x.invited_by_user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                    table.ForeignKey(
                        name: "FK_project_invitations_users_invited_user_id",
                        column: x => x.invited_user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.SetNull);
                });

            migrationBuilder.CreateIndex(
                name: "IX_projects_project_idea_id",
                table: "projects",
                column: "project_idea_id",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_project_invitations_invited_by_user_id",
                table: "project_invitations",
                column: "invited_by_user_id");

            migrationBuilder.CreateIndex(
                name: "IX_project_invitations_invited_user_id",
                table: "project_invitations",
                column: "invited_user_id");

            migrationBuilder.CreateIndex(
                name: "IX_project_invitations_project_id_invited_email_status",
                table: "project_invitations",
                columns: new[] { "project_id", "invited_email", "status" });

            migrationBuilder.CreateIndex(
                name: "IX_project_invitations_teammate_request_id",
                table: "project_invitations",
                column: "teammate_request_id");

            migrationBuilder.CreateIndex(
                name: "IX_project_invitations_token_hash",
                table: "project_invitations",
                column: "token_hash",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_project_members_project_id_user_id",
                table: "project_members",
                columns: new[] { "project_id", "user_id" },
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_project_members_user_id_status",
                table: "project_members",
                columns: new[] { "user_id", "status" });

            migrationBuilder.CreateIndex(
                name: "IX_teammate_requests_domain_status",
                table: "teammate_requests",
                columns: new[] { "domain", "status" });

            migrationBuilder.CreateIndex(
                name: "IX_teammate_requests_matched_by_supervisor_id",
                table: "teammate_requests",
                column: "matched_by_supervisor_id");

            migrationBuilder.CreateIndex(
                name: "IX_teammate_requests_matched_user_id",
                table: "teammate_requests",
                column: "matched_user_id");

            migrationBuilder.CreateIndex(
                name: "IX_teammate_requests_project_id_status",
                table: "teammate_requests",
                columns: new[] { "project_id", "status" });

            migrationBuilder.CreateIndex(
                name: "IX_teammate_requests_requested_by_user_id",
                table: "teammate_requests",
                column: "requested_by_user_id");

            migrationBuilder.AddForeignKey(
                name: "FK_projects_project_ideas_project_idea_id",
                table: "projects",
                column: "project_idea_id",
                principalTable: "project_ideas",
                principalColumn: "id",
                onDelete: ReferentialAction.Restrict);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name: "FK_projects_project_ideas_project_idea_id",
                table: "projects");

            migrationBuilder.DropTable(
                name: "project_invitations");

            migrationBuilder.DropTable(
                name: "project_members");

            migrationBuilder.DropTable(
                name: "teammate_requests");

            migrationBuilder.DropIndex(
                name: "IX_projects_project_idea_id",
                table: "projects");

            migrationBuilder.DropColumn(
                name: "maximum_members",
                table: "projects");

            migrationBuilder.DropColumn(
                name: "project_idea_id",
                table: "projects");
        }
    }
}
