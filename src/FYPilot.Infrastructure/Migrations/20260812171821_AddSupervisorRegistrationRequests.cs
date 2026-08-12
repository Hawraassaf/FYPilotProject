using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddSupervisorRegistrationRequests : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "supervisor_registration_requests",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    full_name = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: false),
                    email = table.Column<string>(type: "character varying(256)", maxLength: 256, nullable: false),
                    password_hash = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: true),
                    academic_title = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: true),
                    university = table.Column<string>(type: "character varying(150)", maxLength: 150, nullable: true),
                    department = table.Column<string>(type: "character varying(150)", maxLength: 150, nullable: true),
                    specialization = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: true),
                    professional_profile_url = table.Column<string>(type: "character varying(300)", maxLength: 300, nullable: true),
                    verification_code_hash = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: true),
                    verification_sent_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    verification_expires_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    verification_failed_attempt_count = table.Column<int>(type: "integer", nullable: false, defaultValue: 0),
                    status = table.Column<string>(type: "character varying(30)", maxLength: 30, nullable: false, defaultValue: "pending_email"),
                    created_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    verified_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    submitted_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    reviewed_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    reviewed_by_admin_id = table.Column<int>(type: "integer", nullable: true),
                    rejection_reason = table.Column<string>(type: "character varying(500)", maxLength: 500, nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_supervisor_registration_requests", x => x.id);
                    table.ForeignKey(
                        name: "FK_supervisor_registration_requests_users_reviewed_by_admin_id",
                        column: x => x.reviewed_by_admin_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.SetNull);
                });

            migrationBuilder.CreateIndex(
                name: "IX_supervisor_registration_requests_created_at_utc",
                table: "supervisor_registration_requests",
                column: "created_at_utc");

            migrationBuilder.CreateIndex(
                name: "IX_supervisor_registration_requests_email",
                table: "supervisor_registration_requests",
                column: "email",
                unique: true,
                filter: "\"status\" IN ('pending_email', 'awaiting_details', 'pending_admin')");

            migrationBuilder.CreateIndex(
                name: "IX_supervisor_registration_requests_reviewed_by_admin_id",
                table: "supervisor_registration_requests",
                column: "reviewed_by_admin_id");

            migrationBuilder.CreateIndex(
                name: "IX_supervisor_registration_requests_status",
                table: "supervisor_registration_requests",
                column: "status");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "supervisor_registration_requests");
        }
    }
}
