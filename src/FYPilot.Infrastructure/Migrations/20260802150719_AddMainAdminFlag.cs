using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddMainAdminFlag : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<bool>(
                name: "is_main_admin",
                table: "users",
                type: "boolean",
                nullable: false,
                defaultValue: false);

            migrationBuilder.CreateIndex(
                name: "IX_users_is_main_admin",
                table: "users",
                column: "is_main_admin",
                unique: true,
                filter: "\"is_main_admin\" = TRUE");

            migrationBuilder.Sql("""
                
                UPDATE users
                SET is_main_admin = TRUE
                WHERE LOWER(email) = 'admin@fyp.com';
                
                """);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_users_is_main_admin",
                table: "users");

            migrationBuilder.DropColumn(
                name: "is_main_admin",
                table: "users");
        }
    }
}
