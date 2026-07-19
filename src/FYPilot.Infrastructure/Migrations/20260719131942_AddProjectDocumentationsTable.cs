using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddProjectDocumentationsTable : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "project_documentations",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    UserId = table.Column<int>(type: "integer", nullable: false),
                    ProjectIdeaId = table.Column<int>(type: "integer", nullable: false),
                    Title = table.Column<string>(type: "text", nullable: false),
                    FunctionalRequirementsJson = table.Column<string>(type: "text", nullable: false),
                    NonFunctionalRequirementsJson = table.Column<string>(type: "text", nullable: false),
                    UseCasesJson = table.Column<string>(type: "text", nullable: false),
                    EdgeCasesJson = table.Column<string>(type: "text", nullable: false),
                    DatabaseDesignJson = table.Column<string>(type: "text", nullable: false),
                    UiDesignJson = table.Column<string>(type: "text", nullable: false),
                    DiagramDescriptionsJson = table.Column<string>(type: "text", nullable: false),
                    AiTechnicalReportJson = table.Column<string>(type: "text", nullable: false),
                    SupervisorStatus = table.Column<string>(type: "text", nullable: false),
                    SupervisorComment = table.Column<string>(type: "text", nullable: false),
                    CreatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    UpdatedAt = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_project_documentations", x => x.Id);
                });
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "project_documentations");
        }
    }
}
