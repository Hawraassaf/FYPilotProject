using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddProjectArchiveAndRemoval : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_project_members_user_id_status",
                table: "project_members");

            migrationBuilder.AddColumn<DateTime>(
                name: "deleted_at_utc",
                table: "projects",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.AddColumn<int>(
                name: "deleted_by_user_id",
                table: "projects",
                type: "integer",
                nullable: true);

            migrationBuilder.AddColumn<bool>(
                name: "is_deleted",
                table: "projects",
                type: "boolean",
                nullable: false,
                defaultValue: false);

            migrationBuilder.AddColumn<DateTime>(
                name: "archived_at_utc",
                table: "project_members",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.AddColumn<bool>(
                name: "is_archived",
                table: "project_members",
                type: "boolean",
                nullable: false,
                defaultValue: false);

            migrationBuilder.AddColumn<DateTime>(
                name: "removed_at_utc",
                table: "project_members",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.CreateIndex(
                name: "IX_projects_deleted_by_user_id",
                table: "projects",
                column: "deleted_by_user_id");

            migrationBuilder.CreateIndex(
                name: "IX_projects_is_deleted_student_id",
                table: "projects",
                columns: new[] { "is_deleted", "student_id" });

            migrationBuilder.CreateIndex(
                name: "IX_project_members_project_id_status",
                table: "project_members",
                columns: new[] { "project_id", "status" });

            migrationBuilder.CreateIndex(
                name: "IX_project_members_user_id_status_is_archived",
                table: "project_members",
                columns: new[] { "user_id", "status", "is_archived" });

            migrationBuilder.AddForeignKey(
                name: "FK_projects_users_deleted_by_user_id",
                table: "projects",
                column: "deleted_by_user_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete: ReferentialAction.SetNull);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name: "FK_projects_users_deleted_by_user_id",
                table: "projects");

            migrationBuilder.DropIndex(
                name: "IX_projects_deleted_by_user_id",
                table: "projects");

            migrationBuilder.DropIndex(
                name: "IX_projects_is_deleted_student_id",
                table: "projects");

            migrationBuilder.DropIndex(
                name: "IX_project_members_project_id_status",
                table: "project_members");

            migrationBuilder.DropIndex(
                name: "IX_project_members_user_id_status_is_archived",
                table: "project_members");

            migrationBuilder.DropColumn(
                name: "deleted_at_utc",
                table: "projects");

            migrationBuilder.DropColumn(
                name: "deleted_by_user_id",
                table: "projects");

            migrationBuilder.DropColumn(
                name: "is_deleted",
                table: "projects");

            migrationBuilder.DropColumn(
                name: "archived_at_utc",
                table: "project_members");

            migrationBuilder.DropColumn(
                name: "is_archived",
                table: "project_members");

            migrationBuilder.DropColumn(
                name: "removed_at_utc",
                table: "project_members");

            migrationBuilder.CreateIndex(
                name: "IX_project_members_user_id_status",
                table: "project_members",
                columns: new[] { "user_id", "status" });
        }
    }
}
