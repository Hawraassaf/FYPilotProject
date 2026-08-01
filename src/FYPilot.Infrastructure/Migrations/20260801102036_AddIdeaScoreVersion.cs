using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddIdeaScoreVersion : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "score_version",
                table: "project_ideas",
                type: "text",
                nullable: true);

            // Backfill existing rows without recalculating any score. Rows
            // that already carry a saved score explanation were generated
            // by the current (v2) formula -- introduced in the same change
            // that added the *_score_reason columns -- so they are tagged
            // v2 retroactively instead of being mislabeled "legacy". Every
            // other existing row predates that change and is tagged legacy.
            migrationBuilder.Sql(
                "UPDATE project_ideas SET score_version = 'v2' " +
                "WHERE score_version IS NULL " +
                "AND innovation_score_reason IS NOT NULL AND innovation_score_reason <> '';");

            migrationBuilder.Sql(
                "UPDATE project_ideas SET score_version = 'legacy' " +
                "WHERE score_version IS NULL;");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "score_version",
                table: "project_ideas");
        }
    }
}
