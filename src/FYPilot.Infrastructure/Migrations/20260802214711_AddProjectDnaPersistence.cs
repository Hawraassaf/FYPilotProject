using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddProjectDnaPersistence : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "project_dna_analyses",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    project_id = table.Column<int>(type: "integer", nullable: false),
                    project_idea_id = table.Column<int>(type: "integer", nullable: false),
                    generated_by_user_id = table.Column<int>(type: "integer", nullable: false),
                    analysis_json = table.Column<string>(type: "text", nullable: false),
                    llm_used = table.Column<bool>(type: "boolean", nullable: false),
                    source = table.Column<string>(type: "character varying(120)", maxLength: 120, nullable: true),
                    provider = table.Column<string>(type: "character varying(120)", maxLength: 120, nullable: true),
                    model_used = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: true),
                    generated_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_project_dna_analyses", x => x.id);
                    table.ForeignKey(
                        name: "FK_project_dna_analyses_project_ideas_project_idea_id",
                        column: x => x.project_idea_id,
                        principalTable: "project_ideas",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_project_dna_analyses_projects_project_id",
                        column: x => x.project_id,
                        principalTable: "projects",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_project_dna_analyses_users_generated_by_user_id",
                        column: x => x.generated_by_user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateIndex(
                name: "IX_project_dna_analyses_generated_by_user_id",
                table: "project_dna_analyses",
                column: "generated_by_user_id");

            migrationBuilder.CreateIndex(
                name: "IX_project_dna_analyses_project_id_project_idea_id_generated_a~",
                table: "project_dna_analyses",
                columns: new[] { "project_id", "project_idea_id", "generated_at_utc" });

            migrationBuilder.CreateIndex(
                name: "IX_project_dna_analyses_project_idea_id",
                table: "project_dna_analyses",
                column: "project_idea_id");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "project_dna_analyses");
        }
    }
}
