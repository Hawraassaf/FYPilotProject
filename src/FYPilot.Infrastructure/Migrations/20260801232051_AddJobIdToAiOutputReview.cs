using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddJobIdToAiOutputReview : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<Guid>(
                name: "job_id",
                table: "ai_output_reviews",
                type: "uuid",
                nullable: true);

            migrationBuilder.CreateIndex(
                name: "IX_ai_output_reviews_job_id",
                table: "ai_output_reviews",
                column: "job_id",
                unique: true,
                filter: "\"job_id\" IS NOT NULL");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_ai_output_reviews_job_id",
                table: "ai_output_reviews");

            migrationBuilder.DropColumn(
                name: "job_id",
                table: "ai_output_reviews");
        }
    }
}
