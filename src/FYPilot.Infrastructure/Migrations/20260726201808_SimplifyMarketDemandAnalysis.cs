using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class SimplifyMarketDemandAnalysis : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "market_demand_yearly_points");

            migrationBuilder.DropTable(
                name: "market_trend_signals");

            migrationBuilder.DropColumn(
                name: "ForecastGeneratedAt",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "ForecastMae",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "ForecastModel",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "ForecastPointsJson",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "ForecastReady",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "ForecastReliable",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "ForecastStatus",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "ForecastWarning",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "NaiveForecastMae",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "TrendDirection",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "TrendRSquared",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "TrendSlopePerWeek",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "TrendStrength",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "TrendSummary",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "TrendTotalChange",
                table: "market_demand_analysis");

            migrationBuilder.DropColumn(
                name: "TrendVolatility",
                table: "market_demand_analysis");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<DateTime>(
                name: "ForecastGeneratedAt",
                table: "market_demand_analysis",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.AddColumn<decimal>(
                name: "ForecastMae",
                table: "market_demand_analysis",
                type: "numeric",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "ForecastModel",
                table: "market_demand_analysis",
                type: "character varying(120)",
                maxLength: 120,
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "ForecastPointsJson",
                table: "market_demand_analysis",
                type: "text",
                nullable: false,
                defaultValue: "");

            migrationBuilder.AddColumn<bool>(
                name: "ForecastReady",
                table: "market_demand_analysis",
                type: "boolean",
                nullable: false,
                defaultValue: false);

            migrationBuilder.AddColumn<bool>(
                name: "ForecastReliable",
                table: "market_demand_analysis",
                type: "boolean",
                nullable: false,
                defaultValue: false);

            migrationBuilder.AddColumn<string>(
                name: "ForecastStatus",
                table: "market_demand_analysis",
                type: "character varying(80)",
                maxLength: 80,
                nullable: false,
                defaultValue: "");

            migrationBuilder.AddColumn<string>(
                name: "ForecastWarning",
                table: "market_demand_analysis",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<decimal>(
                name: "NaiveForecastMae",
                table: "market_demand_analysis",
                type: "numeric",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "TrendDirection",
                table: "market_demand_analysis",
                type: "character varying(30)",
                maxLength: 30,
                nullable: false,
                defaultValue: "");

            migrationBuilder.AddColumn<decimal>(
                name: "TrendRSquared",
                table: "market_demand_analysis",
                type: "numeric",
                nullable: true);

            migrationBuilder.AddColumn<decimal>(
                name: "TrendSlopePerWeek",
                table: "market_demand_analysis",
                type: "numeric",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "TrendStrength",
                table: "market_demand_analysis",
                type: "character varying(30)",
                maxLength: 30,
                nullable: false,
                defaultValue: "");

            migrationBuilder.AddColumn<string>(
                name: "TrendSummary",
                table: "market_demand_analysis",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<decimal>(
                name: "TrendTotalChange",
                table: "market_demand_analysis",
                type: "numeric",
                nullable: true);

            migrationBuilder.AddColumn<decimal>(
                name: "TrendVolatility",
                table: "market_demand_analysis",
                type: "numeric",
                nullable: true);

            migrationBuilder.CreateTable(
                name: "market_demand_yearly_points",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    MarketDemandAnalysisId = table.Column<int>(type: "integer", nullable: false),
                    AdoptionSignal = table.Column<int>(type: "integer", nullable: false),
                    ConfidenceScore = table.Column<int>(type: "integer", nullable: false),
                    DemandIndex = table.Column<decimal>(type: "numeric(6,2)", precision: 6, scale: 2, nullable: false),
                    EvidenceSummary = table.Column<string>(type: "text", nullable: false),
                    JobDemandSignal = table.Column<int>(type: "integer", nullable: false),
                    ProblemSignal = table.Column<int>(type: "integer", nullable: false),
                    SourceUrlsJson = table.Column<string>(type: "text", nullable: false),
                    TechnologyMomentumSignal = table.Column<int>(type: "integer", nullable: false),
                    Year = table.Column<int>(type: "integer", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_market_demand_yearly_points", x => x.Id);
                    table.ForeignKey(
                        name: "FK_market_demand_yearly_points_market_demand_analysis_MarketDe~",
                        column: x => x.MarketDemandAnalysisId,
                        principalTable: "market_demand_analysis",
                        principalColumn: "Id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "market_trend_signals",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    MarketDemandAnalysisId = table.Column<int>(type: "integer", nullable: false),
                    Direction = table.Column<string>(type: "character varying(30)", maxLength: 30, nullable: false),
                    Evidence = table.Column<string>(type: "text", nullable: false),
                    SourceUrl = table.Column<string>(type: "character varying(2000)", maxLength: 2000, nullable: true),
                    Topic = table.Column<string>(type: "character varying(300)", maxLength: 300, nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_market_trend_signals", x => x.Id);
                    table.ForeignKey(
                        name: "FK_market_trend_signals_market_demand_analysis_MarketDemandAna~",
                        column: x => x.MarketDemandAnalysisId,
                        principalTable: "market_demand_analysis",
                        principalColumn: "Id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "IX_market_demand_yearly_points_MarketDemandAnalysisId_Year",
                table: "market_demand_yearly_points",
                columns: new[] { "MarketDemandAnalysisId", "Year" },
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_market_trend_signals_MarketDemandAnalysisId",
                table: "market_trend_signals",
                column: "MarketDemandAnalysisId");
        }
    }
}
