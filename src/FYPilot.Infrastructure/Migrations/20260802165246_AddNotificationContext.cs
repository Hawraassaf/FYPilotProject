using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddNotificationContext : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.Sql(
             """
            DROP INDEX IF EXISTS "IX_notifications_recipient_user_id";
            """);

            migrationBuilder.AlterColumn<string>(
                name: "url",
                table: "notifications",
                type: "character varying(500)",
                maxLength: 500,
                nullable: false,
                oldClrType: typeof(string),
                oldType: "text");

            migrationBuilder.AlterColumn<string>(
                name: "type",
                table: "notifications",
                type: "character varying(80)",
                maxLength: 80,
                nullable: false,
                defaultValue: "general",
                oldClrType: typeof(string),
                oldType: "text");

            migrationBuilder.AlterColumn<string>(
                name: "title",
                table: "notifications",
                type: "character varying(200)",
                maxLength: 200,
                nullable: false,
                oldClrType: typeof(string),
                oldType: "text");

            migrationBuilder.AlterColumn<string>(
                name: "message",
                table: "notifications",
                type: "character varying(1200)",
                maxLength: 1200,
                nullable: false,
                oldClrType: typeof(string),
                oldType: "text");

            migrationBuilder.AddColumn<int>(
                name: "actor_user_id",
                table: "notifications",
                type: "integer",
                nullable: true);

            migrationBuilder.AddColumn<int>(
                name: "project_id",
                table: "notifications",
                type: "integer",
                nullable: true);

            migrationBuilder.CreateIndex(
                name: "IX_notifications_actor_user_id",
                table: "notifications",
                column: "actor_user_id");

            migrationBuilder.CreateIndex(
                name: "IX_notifications_project_id",
                table: "notifications",
                column: "project_id");

            migrationBuilder.CreateIndex(
                name: "IX_notifications_recipient_user_id_created_at",
                table: "notifications",
                columns: new[] { "recipient_user_id", "created_at" });

            migrationBuilder.CreateIndex(
                name: "IX_notifications_recipient_user_id_is_read_created_at",
                table: "notifications",
                columns: new[] { "recipient_user_id", "is_read", "created_at" });

            migrationBuilder.AddForeignKey(
                name: "FK_notifications_projects_project_id",
                table: "notifications",
                column: "project_id",
                principalTable: "projects",
                principalColumn: "id",
                onDelete: ReferentialAction.Cascade);

            migrationBuilder.AddForeignKey(
                name: "FK_notifications_users_actor_user_id",
                table: "notifications",
                column: "actor_user_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete: ReferentialAction.SetNull);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name: "FK_notifications_projects_project_id",
                table: "notifications");

            migrationBuilder.DropForeignKey(
                name: "FK_notifications_users_actor_user_id",
                table: "notifications");

            migrationBuilder.DropIndex(
                name: "IX_notifications_actor_user_id",
                table: "notifications");

            migrationBuilder.DropIndex(
                name: "IX_notifications_project_id",
                table: "notifications");

            migrationBuilder.DropIndex(
                name: "IX_notifications_recipient_user_id_created_at",
                table: "notifications");

            migrationBuilder.DropIndex(
                name: "IX_notifications_recipient_user_id_is_read_created_at",
                table: "notifications");

            migrationBuilder.DropColumn(
                name: "actor_user_id",
                table: "notifications");

            migrationBuilder.DropColumn(
                name: "project_id",
                table: "notifications");

            migrationBuilder.AlterColumn<string>(
                name: "url",
                table: "notifications",
                type: "text",
                nullable: false,
                oldClrType: typeof(string),
                oldType: "character varying(500)",
                oldMaxLength: 500);

            migrationBuilder.AlterColumn<string>(
                name: "type",
                table: "notifications",
                type: "text",
                nullable: false,
                oldClrType: typeof(string),
                oldType: "character varying(80)",
                oldMaxLength: 80,
                oldDefaultValue: "general");

            migrationBuilder.AlterColumn<string>(
                name: "title",
                table: "notifications",
                type: "text",
                nullable: false,
                oldClrType: typeof(string),
                oldType: "character varying(200)",
                oldMaxLength: 200);

            migrationBuilder.AlterColumn<string>(
                name: "message",
                table: "notifications",
                type: "text",
                nullable: false,
                oldClrType: typeof(string),
                oldType: "character varying(1200)",
                oldMaxLength: 1200);

            migrationBuilder.CreateIndex(
                name: "IX_notifications_recipient_user_id",
                table: "notifications",
                column: "recipient_user_id");
        }
    }
}
