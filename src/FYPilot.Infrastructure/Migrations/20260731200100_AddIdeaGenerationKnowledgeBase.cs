using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddIdeaGenerationKnowledgeBase : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "historical_fyp_projects",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    Title = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: false),
                    ProblemStatement = table.Column<string>(type: "character varying(4000)", maxLength: 4000, nullable: false),
                    Major = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: true),
                    Domain = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: true),
                    TargetUsers = table.Column<string>(type: "character varying(500)", maxLength: 500, nullable: true),
                    Technologies = table.Column<string>(type: "character varying(1000)", maxLength: 1000, nullable: false),
                    CompletionYear = table.Column<int>(type: "integer", nullable: true),
                    ProjectStatus = table.Column<string>(type: "character varying(30)", maxLength: 30, nullable: false),
                    Keywords = table.Column<string>(type: "character varying(1000)", maxLength: 1000, nullable: false),
                    ExcludeSimilarIdeas = table.Column<bool>(type: "boolean", nullable: false),
                    AllowAsInspiration = table.Column<bool>(type: "boolean", nullable: false),
                    ExclusionReason = table.Column<string>(type: "character varying(1000)", maxLength: 1000, nullable: true),
                    IsActive = table.Column<bool>(type: "boolean", nullable: false),
                    CreatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    UpdatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    CreatedByUserId = table.Column<int>(type: "integer", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_historical_fyp_projects", x => x.Id);
                    table.ForeignKey(
                        name: "FK_historical_fyp_projects_users_CreatedByUserId",
                        column: x => x.CreatedByUserId,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateTable(
                name: "idea_generation_guidances",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    Title = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: false),
                    Content = table.Column<string>(type: "character varying(4000)", maxLength: 4000, nullable: false),
                    GuidanceType = table.Column<string>(type: "character varying(40)", maxLength: 40, nullable: false),
                    Major = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: true),
                    Domain = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: true),
                    Priority = table.Column<int>(type: "integer", nullable: false),
                    IsActive = table.Column<bool>(type: "boolean", nullable: false),
                    EffectiveFrom = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    EffectiveUntil = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    CreatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    UpdatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    CreatedByUserId = table.Column<int>(type: "integer", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_idea_generation_guidances", x => x.Id);
                    table.ForeignKey(
                        name: "FK_idea_generation_guidances_users_CreatedByUserId",
                        column: x => x.CreatedByUserId,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateTable(
                name: "historical_fyp_future_opportunities",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    HistoricalFypProjectId = table.Column<int>(type: "integer", nullable: false),
                    Title = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: false),
                    Description = table.Column<string>(type: "character varying(4000)", maxLength: 4000, nullable: false),
                    SuggestedDomain = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: true),
                    SuggestedTechnologies = table.Column<string>(type: "character varying(1000)", maxLength: 1000, nullable: false),
                    ResearchGap = table.Column<string>(type: "character varying(2000)", maxLength: 2000, nullable: true),
                    Priority = table.Column<int>(type: "integer", nullable: false),
                    IsActive = table.Column<bool>(type: "boolean", nullable: false),
                    CreatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    UpdatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    CreatedByUserId = table.Column<int>(type: "integer", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_historical_fyp_future_opportunities", x => x.Id);
                    table.ForeignKey(
                        name: "FK_historical_fyp_future_opportunities_historical_fyp_projects~",
                        column: x => x.HistoricalFypProjectId,
                        principalTable: "historical_fyp_projects",
                        principalColumn: "Id",
                        onDelete: ReferentialAction.Restrict);
                    table.ForeignKey(
                        name: "FK_historical_fyp_future_opportunities_users_CreatedByUserId",
                        column: x => x.CreatedByUserId,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateIndex(
                name: "IX_historical_fyp_future_opportunities_CreatedByUserId",
                table: "historical_fyp_future_opportunities",
                column: "CreatedByUserId");

            migrationBuilder.CreateIndex(
                name: "IX_historical_fyp_future_opportunities_HistoricalFypProjectId",
                table: "historical_fyp_future_opportunities",
                column: "HistoricalFypProjectId");

            migrationBuilder.CreateIndex(
                name: "IX_historical_fyp_future_opportunities_IsActive",
                table: "historical_fyp_future_opportunities",
                column: "IsActive");

            migrationBuilder.CreateIndex(
                name: "IX_historical_fyp_future_opportunities_Priority",
                table: "historical_fyp_future_opportunities",
                column: "Priority");

            migrationBuilder.CreateIndex(
                name: "IX_historical_fyp_projects_CompletionYear",
                table: "historical_fyp_projects",
                column: "CompletionYear");

            migrationBuilder.CreateIndex(
                name: "IX_historical_fyp_projects_CreatedByUserId",
                table: "historical_fyp_projects",
                column: "CreatedByUserId");

            migrationBuilder.CreateIndex(
                name: "IX_historical_fyp_projects_Domain",
                table: "historical_fyp_projects",
                column: "Domain");

            migrationBuilder.CreateIndex(
                name: "IX_historical_fyp_projects_ExcludeSimilarIdeas",
                table: "historical_fyp_projects",
                column: "ExcludeSimilarIdeas");

            migrationBuilder.CreateIndex(
                name: "IX_historical_fyp_projects_IsActive",
                table: "historical_fyp_projects",
                column: "IsActive");

            migrationBuilder.CreateIndex(
                name: "IX_historical_fyp_projects_Major",
                table: "historical_fyp_projects",
                column: "Major");

            migrationBuilder.CreateIndex(
                name: "IX_idea_generation_guidances_CreatedByUserId",
                table: "idea_generation_guidances",
                column: "CreatedByUserId");

            migrationBuilder.CreateIndex(
                name: "IX_idea_generation_guidances_Domain",
                table: "idea_generation_guidances",
                column: "Domain");

            migrationBuilder.CreateIndex(
                name: "IX_idea_generation_guidances_IsActive",
                table: "idea_generation_guidances",
                column: "IsActive");

            migrationBuilder.CreateIndex(
                name: "IX_idea_generation_guidances_Major",
                table: "idea_generation_guidances",
                column: "Major");

            migrationBuilder.CreateIndex(
                name: "IX_idea_generation_guidances_Priority",
                table: "idea_generation_guidances",
                column: "Priority");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "historical_fyp_future_opportunities");

            migrationBuilder.DropTable(
                name: "idea_generation_guidances");

            migrationBuilder.DropTable(
                name: "historical_fyp_projects");
        }
    }
}
