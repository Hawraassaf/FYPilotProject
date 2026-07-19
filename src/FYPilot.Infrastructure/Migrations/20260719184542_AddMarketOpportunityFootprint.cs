using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddMarketOpportunityFootprint : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "market_opportunity_snapshots",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    ProjectIdeaId = table.Column<int>(type: "integer", nullable: false),
                    UserId = table.Column<int>(type: "integer", nullable: false),
                    Status = table.Column<string>(type: "character varying(40)", maxLength: 40, nullable: false),
                    OverallOpportunityScore = table.Column<int>(type: "integer", nullable: true),
                    OverallConfidenceScore = table.Column<int>(type: "integer", nullable: false),
                    OverallDemandLevel = table.Column<string>(type: "character varying(30)", maxLength: 30, nullable: false),
                    BestLaunchMarket = table.Column<string>(type: "character varying(120)", maxLength: 120, nullable: false),
                    BestLaunchReason = table.Column<string>(type: "character varying(2000)", maxLength: 2000, nullable: false),
                    ExpansionPathJson = table.Column<string>(type: "text", nullable: false),
                    WhyDemandedJson = table.Column<string>(type: "text", nullable: false),
                    StrategicRecommendation = table.Column<string>(type: "text", nullable: false),
                    LimitationsJson = table.Column<string>(type: "text", nullable: false),
                    SourcesJson = table.Column<string>(type: "text", nullable: false),
                    GroundedInLiveData = table.Column<bool>(type: "boolean", nullable: false),
                    Provider = table.Column<string>(type: "character varying(120)", maxLength: 120, nullable: false),
                    ModelUsed = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: true),
                    AnalyzedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    CreatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_market_opportunity_snapshots", x => x.Id);
                    table.ForeignKey(
                        name: "FK_market_opportunity_snapshots_project_ideas_ProjectIdeaId",
                        column: x => x.ProjectIdeaId,
                        principalTable: "project_ideas",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_market_opportunity_snapshots_users_UserId",
                        column: x => x.UserId,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "market_opportunity_regions",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    SnapshotId = table.Column<int>(type: "integer", nullable: false),
                    RegionKey = table.Column<string>(type: "character varying(20)", maxLength: 20, nullable: false),
                    RegionName = table.Column<string>(type: "character varying(50)", maxLength: 50, nullable: false),
                    OpportunityScore = table.Column<int>(type: "integer", nullable: true),
                    ConfidenceScore = table.Column<int>(type: "integer", nullable: false),
                    DemandLevel = table.Column<string>(type: "character varying(30)", maxLength: 30, nullable: false),
                    CompetitionPressure = table.Column<string>(type: "character varying(20)", maxLength: 20, nullable: false),
                    EvidenceSummary = table.Column<string>(type: "text", nullable: false),
                    ScoreBreakdownJson = table.Column<string>(type: "text", nullable: false),
                    SourceUrlsJson = table.Column<string>(type: "text", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_market_opportunity_regions", x => x.Id);
                    table.ForeignKey(
                        name: "FK_market_opportunity_regions_market_opportunity_snapshots_Sna~",
                        column: x => x.SnapshotId,
                        principalTable: "market_opportunity_snapshots",
                        principalColumn: "Id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "IX_market_opportunity_regions_SnapshotId_RegionKey",
                table: "market_opportunity_regions",
                columns: new[] { "SnapshotId", "RegionKey" },
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_market_opportunity_snapshots_AnalyzedAt",
                table: "market_opportunity_snapshots",
                column: "AnalyzedAt");

            migrationBuilder.CreateIndex(
                name: "IX_market_opportunity_snapshots_ProjectIdeaId",
                table: "market_opportunity_snapshots",
                column: "ProjectIdeaId");

            migrationBuilder.CreateIndex(
                name: "IX_market_opportunity_snapshots_ProjectIdeaId_AnalyzedAt",
                table: "market_opportunity_snapshots",
                columns: new[] { "ProjectIdeaId", "AnalyzedAt" });

            migrationBuilder.CreateIndex(
                name: "IX_market_opportunity_snapshots_UserId",
                table: "market_opportunity_snapshots",
                column: "UserId");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "market_opportunity_regions");

            migrationBuilder.DropTable(
                name: "market_opportunity_snapshots");
        }
    }
}
