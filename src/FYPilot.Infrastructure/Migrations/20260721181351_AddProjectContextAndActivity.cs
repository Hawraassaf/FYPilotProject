using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddProjectContextAndActivity : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<int>(
                name: "last_active_project_id",
                table: "users",
                type: "integer",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "last_project_page",
                table: "users",
                type: "character varying(200)",
                maxLength: 200,
                nullable: true);

            migrationBuilder.AddColumn<DateTime>(
                name: "last_project_visited_at_utc",
                table: "users",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.CreateTable(
                name: "project_activities",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    project_id = table.Column<int>(type: "integer", nullable: false),
                    user_id = table.Column<int>(type: "integer", nullable: false),
                    action_type = table.Column<string>(type: "character varying(80)", maxLength: 80, nullable: false),
                    description = table.Column<string>(type: "character varying(1000)", maxLength: 1000, nullable: false),
                    previous_idea_id = table.Column<int>(type: "integer", nullable: true),
                    new_idea_id = table.Column<int>(type: "integer", nullable: true),
                    created_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_project_activities", x => x.id);
                    table.ForeignKey(
                        name: "FK_project_activities_project_ideas_new_idea_id",
                        column: x => x.new_idea_id,
                        principalTable: "project_ideas",
                        principalColumn: "id",
                        onDelete: ReferentialAction.SetNull);
                    table.ForeignKey(
                        name: "FK_project_activities_project_ideas_previous_idea_id",
                        column: x => x.previous_idea_id,
                        principalTable: "project_ideas",
                        principalColumn: "id",
                        onDelete: ReferentialAction.SetNull);
                    table.ForeignKey(
                        name: "FK_project_activities_projects_project_id",
                        column: x => x.project_id,
                        principalTable: "projects",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_project_activities_users_user_id",
                        column: x => x.user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateIndex(
                name: "IX_users_last_active_project_id",
                table: "users",
                column: "last_active_project_id");

            migrationBuilder.CreateIndex(
                name: "IX_project_activities_new_idea_id",
                table: "project_activities",
                column: "new_idea_id");

            migrationBuilder.CreateIndex(
                name: "IX_project_activities_previous_idea_id",
                table: "project_activities",
                column: "previous_idea_id");

            migrationBuilder.CreateIndex(
                name: "IX_project_activities_project_id_created_at_utc",
                table: "project_activities",
                columns: new[] { "project_id", "created_at_utc" });

            migrationBuilder.CreateIndex(
                name: "IX_project_activities_user_id",
                table: "project_activities",
                column: "user_id");

            migrationBuilder.AddForeignKey(
                name: "FK_users_projects_last_active_project_id",
                table: "users",
                column: "last_active_project_id",
                principalTable: "projects",
                principalColumn: "id",
                onDelete: ReferentialAction.SetNull);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name: "FK_users_projects_last_active_project_id",
                table: "users");

            migrationBuilder.DropTable(
                name: "project_activities");

            migrationBuilder.DropIndex(
                name: "IX_users_last_active_project_id",
                table: "users");

            migrationBuilder.DropColumn(
                name: "last_active_project_id",
                table: "users");

            migrationBuilder.DropColumn(
                name: "last_project_page",
                table: "users");

            migrationBuilder.DropColumn(
                name: "last_project_visited_at_utc",
                table: "users");
        }
    }
}
