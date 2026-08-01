using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddIdeaGenerationScoreReasons : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "feasibility_score_reason",
                table: "project_ideas",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "innovation_score_reason",
                table: "project_ideas",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "market_demand_score_reason",
                table: "project_ideas",
                type: "text",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "feasibility_score_reason",
                table: "project_ideas");

            migrationBuilder.DropColumn(
                name: "innovation_score_reason",
                table: "project_ideas");

            migrationBuilder.DropColumn(
                name: "market_demand_score_reason",
                table: "project_ideas");
        }
    }
}
