using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddProjectScopedCandidateIdeas : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<int>(
                name: "generated_for_project_id",
                table: "project_ideas",
                type: "integer",
                nullable: true);

            /*
    * Existing official selected ideas already belong
    * logically to their current projects.
    */
            migrationBuilder.Sql(
                """
    UPDATE project_ideas AS idea
    SET generated_for_project_id = project.id
    FROM projects AS project
    WHERE project.project_idea_id = idea.id
      AND idea.generated_for_project_id IS NULL;
    """);
            migrationBuilder.CreateIndex(
                name: "IX_project_ideas_generated_for_project_id_created_at",
                table: "project_ideas",
                columns: new[] { "generated_for_project_id", "created_at" });

            migrationBuilder.AddForeignKey(
                name: "FK_project_ideas_projects_generated_for_project_id",
                table: "project_ideas",
                column: "generated_for_project_id",
                principalTable: "projects",
                principalColumn: "id",
                onDelete: ReferentialAction.SetNull);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name: "FK_project_ideas_projects_generated_for_project_id",
                table: "project_ideas");

            migrationBuilder.DropIndex(
                name: "IX_project_ideas_generated_for_project_id_created_at",
                table: "project_ideas");

            migrationBuilder.DropColumn(
                name: "generated_for_project_id",
                table: "project_ideas");
        }
    }
}
