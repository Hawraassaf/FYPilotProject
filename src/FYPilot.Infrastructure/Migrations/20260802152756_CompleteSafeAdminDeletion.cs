using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class CompleteSafeAdminDeletion : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name: "FK_historical_fyp_future_opportunities_users_CreatedByUserId",
                table: "historical_fyp_future_opportunities");

            migrationBuilder.DropForeignKey(
                name: "FK_historical_fyp_projects_users_CreatedByUserId",
                table: "historical_fyp_projects");

            migrationBuilder.DropForeignKey(
                name: "FK_idea_generation_guidances_users_CreatedByUserId",
                table: "idea_generation_guidances");

            migrationBuilder.DropForeignKey(
                name: "FK_project_activities_users_user_id",
                table: "project_activities");

            migrationBuilder.AlterColumn<int>(
                name: "user_id",
                table: "project_activities",
                type: "integer",
                nullable: true,
                oldClrType: typeof(int),
                oldType: "integer");

            migrationBuilder.AddForeignKey(
                name: "FK_historical_fyp_future_opportunities_users_CreatedByUserId",
                table: "historical_fyp_future_opportunities",
                column: "CreatedByUserId",
                principalTable: "users",
                principalColumn: "id",
                onDelete: ReferentialAction.SetNull);

            migrationBuilder.AddForeignKey(
                name: "FK_historical_fyp_projects_users_CreatedByUserId",
                table: "historical_fyp_projects",
                column: "CreatedByUserId",
                principalTable: "users",
                principalColumn: "id",
                onDelete: ReferentialAction.SetNull);

            migrationBuilder.AddForeignKey(
                name: "FK_idea_generation_guidances_users_CreatedByUserId",
                table: "idea_generation_guidances",
                column: "CreatedByUserId",
                principalTable: "users",
                principalColumn: "id",
                onDelete: ReferentialAction.SetNull);

            migrationBuilder.AddForeignKey(
                name: "FK_project_activities_users_user_id",
                table: "project_activities",
                column: "user_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete: ReferentialAction.SetNull);
            migrationBuilder.Sql("""
                UPDATE users
                SET is_main_admin = CASE
                    WHEN LOWER(email) = 'admin@fyp.com' THEN TRUE
                    ELSE FALSE
                END;
            """);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name: "FK_historical_fyp_future_opportunities_users_CreatedByUserId",
                table: "historical_fyp_future_opportunities");

            migrationBuilder.DropForeignKey(
                name: "FK_historical_fyp_projects_users_CreatedByUserId",
                table: "historical_fyp_projects");

            migrationBuilder.DropForeignKey(
                name: "FK_idea_generation_guidances_users_CreatedByUserId",
                table: "idea_generation_guidances");

            migrationBuilder.DropForeignKey(
                name: "FK_project_activities_users_user_id",
                table: "project_activities");

            migrationBuilder.AlterColumn<int>(
                name: "user_id",
                table: "project_activities",
                type: "integer",
                nullable: false,
                defaultValue: 0,
                oldClrType: typeof(int),
                oldType: "integer",
                oldNullable: true);

            migrationBuilder.AddForeignKey(
                name: "FK_historical_fyp_future_opportunities_users_CreatedByUserId",
                table: "historical_fyp_future_opportunities",
                column: "CreatedByUserId",
                principalTable: "users",
                principalColumn: "id",
                onDelete: ReferentialAction.Restrict);

            migrationBuilder.AddForeignKey(
                name: "FK_historical_fyp_projects_users_CreatedByUserId",
                table: "historical_fyp_projects",
                column: "CreatedByUserId",
                principalTable: "users",
                principalColumn: "id",
                onDelete: ReferentialAction.Restrict);

            migrationBuilder.AddForeignKey(
                name: "FK_idea_generation_guidances_users_CreatedByUserId",
                table: "idea_generation_guidances",
                column: "CreatedByUserId",
                principalTable: "users",
                principalColumn: "id",
                onDelete: ReferentialAction.Restrict);

            migrationBuilder.AddForeignKey(
                name: "FK_project_activities_users_user_id",
                table: "project_activities",
                column: "user_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete: ReferentialAction.Restrict);
        }
    }
}
