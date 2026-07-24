using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class EnhanceProjectDiscussion : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<DateTime>(
                name: "deleted_at_utc",
                table: "project_discussion_messages",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.AddColumn<DateTime>(
                name: "edited_at_utc",
                table: "project_discussion_messages",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.AddColumn<bool>(
                name: "is_deleted",
                table: "project_discussion_messages",
                type: "boolean",
                nullable: false,
                defaultValue: false);

            migrationBuilder.AddColumn<bool>(
                name: "is_edited",
                table: "project_discussion_messages",
                type: "boolean",
                nullable: false,
                defaultValue: false);

            migrationBuilder.AddColumn<int>(
                name: "reply_to_message_id",
                table: "project_discussion_messages",
                type: "integer",
                nullable: true);

            migrationBuilder.CreateTable(
                name: "project_discussion_attachments",
                columns: table => new
                {
                    id = table.Column<int>(type: "integer", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    message_id = table.Column<int>(type: "integer", nullable: false),
                    original_file_name = table.Column<string>(type: "character varying(255)", maxLength: 255, nullable: false),
                    stored_file_name = table.Column<string>(type: "character varying(255)", maxLength: 255, nullable: false),
                    content_type = table.Column<string>(type: "character varying(150)", maxLength: 150, nullable: false),
                    size_bytes = table.Column<long>(type: "bigint", nullable: false),
                    relative_path = table.Column<string>(type: "character varying(500)", maxLength: 500, nullable: false),
                    uploaded_at_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_project_discussion_attachments", x => x.id);
                    table.ForeignKey(
                        name: "FK_project_discussion_attachments_project_discussion_messages_~",
                        column: x => x.message_id,
                        principalTable: "project_discussion_messages",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "IX_project_discussion_messages_reply_to_message_id",
                table: "project_discussion_messages",
                column: "reply_to_message_id");

            migrationBuilder.CreateIndex(
                name: "IX_project_discussion_attachments_message_id",
                table: "project_discussion_attachments",
                column: "message_id");

            migrationBuilder.AddForeignKey(
                name: "FK_project_discussion_messages_project_discussion_messages_rep~",
                table: "project_discussion_messages",
                column: "reply_to_message_id",
                principalTable: "project_discussion_messages",
                principalColumn: "id",
                onDelete: ReferentialAction.Restrict);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name: "FK_project_discussion_messages_project_discussion_messages_rep~",
                table: "project_discussion_messages");

            migrationBuilder.DropTable(
                name: "project_discussion_attachments");

            migrationBuilder.DropIndex(
                name: "IX_project_discussion_messages_reply_to_message_id",
                table: "project_discussion_messages");

            migrationBuilder.DropColumn(
                name: "deleted_at_utc",
                table: "project_discussion_messages");

            migrationBuilder.DropColumn(
                name: "edited_at_utc",
                table: "project_discussion_messages");

            migrationBuilder.DropColumn(
                name: "is_deleted",
                table: "project_discussion_messages");

            migrationBuilder.DropColumn(
                name: "is_edited",
                table: "project_discussion_messages");

            migrationBuilder.DropColumn(
                name: "reply_to_message_id",
                table: "project_discussion_messages");
        }
    }
}
