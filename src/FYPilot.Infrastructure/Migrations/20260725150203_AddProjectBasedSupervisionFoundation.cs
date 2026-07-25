using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddProjectBasedSupervisionFoundation
        : Migration
    {
        /// <inheritdoc />
        protected override void Up(
            MigrationBuilder migrationBuilder)
        {
            /*
             * Existing databases may use different
             * foreign-key names.
             *
             * Discover and remove the real foreign keys
             * attached to the relevant meetings columns.
             */
            migrationBuilder.Sql(
                """
                DO $$
                DECLARE current_constraint text;
                BEGIN
                    FOR current_constraint IN
                        SELECT DISTINCT
                            constraint_record.conname
                        FROM pg_constraint
                            AS constraint_record
                        INNER JOIN pg_class
                            AS table_record
                            ON table_record.oid =
                               constraint_record.conrelid
                        INNER JOIN pg_namespace
                            AS schema_record
                            ON schema_record.oid =
                               table_record.relnamespace
                        INNER JOIN pg_attribute
                            AS column_record
                            ON column_record.attrelid =
                               table_record.oid
                           AND column_record.attnum =
                               ANY(
                                   constraint_record.conkey)
                        WHERE schema_record.nspname =
                                  'public'
                          AND table_record.relname =
                                  'meetings'
                          AND constraint_record.contype =
                                  'f'
                          AND column_record.attname IN
                              (
                                  'student_id',
                                  'supervisor_id'
                              )
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE public.meetings '
                            || 'DROP CONSTRAINT IF EXISTS %I',
                            current_constraint);
                    END LOOP;
                END
                $$;
                """);

            /*
             * Discover and remove the real foreign keys
             * attached to supervisor assignments.
             */
            migrationBuilder.Sql(
                """
                DO $$
                DECLARE current_constraint text;
                BEGIN
                    FOR current_constraint IN
                        SELECT DISTINCT
                            constraint_record.conname
                        FROM pg_constraint
                            AS constraint_record
                        INNER JOIN pg_class
                            AS table_record
                            ON table_record.oid =
                               constraint_record.conrelid
                        INNER JOIN pg_namespace
                            AS schema_record
                            ON schema_record.oid =
                               table_record.relnamespace
                        INNER JOIN pg_attribute
                            AS column_record
                            ON column_record.attrelid =
                               table_record.oid
                           AND column_record.attnum =
                               ANY(
                                   constraint_record.conkey)
                        WHERE schema_record.nspname =
                                  'public'
                          AND table_record.relname =
                                  'supervisor_assignments'
                          AND constraint_record.contype =
                                  'f'
                          AND column_record.attname IN
                              (
                                  'assigned_by_admin_id',
                                  'student_id',
                                  'supervisor_id'
                              )
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE '
                            || 'public.supervisor_assignments '
                            || 'DROP CONSTRAINT IF EXISTS %I',
                            current_constraint);
                    END LOOP;
                END
                $$;
                """);

            /*
             * Older database copies may not contain
             * these indexes. IF EXISTS prevents the
             * migration from failing.
             */
            migrationBuilder.Sql(
                """
                DROP INDEX IF EXISTS
                    public."IX_supervisor_evaluations_supervisor_id";

                DROP INDEX IF EXISTS
                    public."IX_supervisor_assignments_supervisor_id";

                DROP INDEX IF EXISTS
                    public."IX_meetings_supervisor_id";
                """);

            migrationBuilder.AlterColumn<string>(
                name: "status",
                table: "supervisor_evaluations",
                type: "character varying(40)",
                maxLength: 40,
                nullable: false,
                defaultValue: "pending",
                oldClrType: typeof(string),
                oldType: "text");

            migrationBuilder.AddColumn<int>(
                name: "project_id",
                table: "supervisor_evaluations",
                type: "integer",
                nullable: true);

            migrationBuilder.AlterColumn<string>(
                name: "status",
                table: "supervisor_assignments",
                type: "character varying(40)",
                maxLength: 40,
                nullable: false,
                defaultValue: "pending_admin",
                oldClrType: typeof(string),
                oldType: "text");

            migrationBuilder.AddColumn<int>(
                name: "project_id",
                table: "supervisor_assignments",
                type: "integer",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "supervisor_assignment_status",
                table: "projects",
                type: "character varying(40)",
                maxLength: 40,
                nullable: false,
                defaultValue: "unassigned");

            migrationBuilder.AddColumn<int>(
                name: "project_id",
                table: "meetings",
                type: "integer",
                nullable: true);

            /*
             * An existing project with a supervisor
             * becomes active.
             *
             * A project without a supervisor remains
             * unassigned.
             */
            migrationBuilder.Sql(
                """
                UPDATE projects
                SET supervisor_assignment_status =
                    CASE
                        WHEN supervisor_id IS NULL
                            THEN 'unassigned'
                        ELSE 'active'
                    END;
                """);

            /*
             * Safely connect old evaluations to their
             * project using the official project idea.
             *
             * Only ideas referenced by exactly one
             * project are transferred automatically.
             * Ambiguous records remain legacy records
             * with project_id = NULL.
             */
            migrationBuilder.Sql(
                """
                UPDATE supervisor_evaluations
                    AS evaluation
                SET project_id =
                    matching_project.project_id
                FROM
                (
                    SELECT
                        project_idea_id AS idea_id,
                        MIN(id) AS project_id
                    FROM projects
                    WHERE project_idea_id IS NOT NULL
                    GROUP BY project_idea_id
                    HAVING COUNT(*) = 1
                ) AS matching_project
                WHERE matching_project.idea_id =
                      evaluation.idea_id
                  AND evaluation.project_id IS NULL;
                """);

            /*
             * Some older databases may contain more
             * than one evaluation for the same project
             * and idea.
             *
             * Keep the most recently updated evaluation
             * as the current shared evaluation.
             *
             * Older rows remain preserved as legacy
             * history with project_id = NULL.
             */
            migrationBuilder.Sql(
                """
                WITH ranked_evaluations AS
                (
                    SELECT
                        id,
                        ROW_NUMBER() OVER
                        (
                            PARTITION BY
                                project_id,
                                idea_id
                            ORDER BY
                                updated_at DESC,
                                created_at DESC,
                                id DESC
                        ) AS evaluation_order
                    FROM supervisor_evaluations
                    WHERE project_id IS NOT NULL
                )
                UPDATE supervisor_evaluations
                    AS evaluation
                SET project_id = NULL
                FROM ranked_evaluations
                    AS ranked
                WHERE evaluation.id =
                      ranked.id
                  AND ranked.evaluation_order > 1;
                """);

            migrationBuilder.CreateIndex(
                name:
                    "IX_supervisor_evaluations_project_id_idea_id",
                table: "supervisor_evaluations",
                columns: new[]
                {
                    "project_id",
                    "idea_id"
                },
                unique: true,
                filter:
                    "\"project_id\" IS NOT NULL");

            migrationBuilder.CreateIndex(
                name:
                    "IX_supervisor_evaluations_supervisor_id_project_id",
                table: "supervisor_evaluations",
                columns: new[]
                {
                    "supervisor_id",
                    "project_id"
                });

            migrationBuilder.CreateIndex(
                name:
                    "IX_supervisor_assignments_project_id",
                table: "supervisor_assignments",
                column: "project_id",
                unique: true,
                filter:
                    "\"project_id\" IS NOT NULL "
                    + "AND \"status\" IN "
                    + "('pending_admin', 'active')");

            migrationBuilder.CreateIndex(
                name:
                    "IX_supervisor_assignments_supervisor_id_status",
                table: "supervisor_assignments",
                columns: new[]
                {
                    "supervisor_id",
                    "status"
                });

            migrationBuilder.CreateIndex(
                name:
                    "IX_meetings_project_id_scheduled_at",
                table: "meetings",
                columns: new[]
                {
                    "project_id",
                    "scheduled_at"
                });

            migrationBuilder.CreateIndex(
                name:
                    "IX_meetings_supervisor_id_scheduled_at",
                table: "meetings",
                columns: new[]
                {
                    "supervisor_id",
                    "scheduled_at"
                });

            migrationBuilder.AddForeignKey(
                name:
                    "FK_meetings_projects_project_id",
                table: "meetings",
                column: "project_id",
                principalTable: "projects",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.Cascade);

            migrationBuilder.AddForeignKey(
                name:
                    "FK_meetings_users_student_id",
                table: "meetings",
                column: "student_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.SetNull);

            migrationBuilder.AddForeignKey(
                name:
                    "FK_meetings_users_supervisor_id",
                table: "meetings",
                column: "supervisor_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.Restrict);

            migrationBuilder.AddForeignKey(
                name:
                    "FK_supervisor_assignments_projects_project_id",
                table: "supervisor_assignments",
                column: "project_id",
                principalTable: "projects",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.Cascade);

            migrationBuilder.AddForeignKey(
                name:
                    "FK_supervisor_assignments_users_assigned_by_admin_id",
                table: "supervisor_assignments",
                column: "assigned_by_admin_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.SetNull);

            migrationBuilder.AddForeignKey(
                name:
                    "FK_supervisor_assignments_users_student_id",
                table: "supervisor_assignments",
                column: "student_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.Restrict);

            migrationBuilder.AddForeignKey(
                name:
                    "FK_supervisor_assignments_users_supervisor_id",
                table: "supervisor_assignments",
                column: "supervisor_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.Restrict);

            migrationBuilder.AddForeignKey(
                name:
                    "FK_supervisor_evaluations_projects_project_id",
                table: "supervisor_evaluations",
                column: "project_id",
                principalTable: "projects",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.Cascade);
        }

        /// <inheritdoc />
        protected override void Down(
            MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name:
                    "FK_meetings_projects_project_id",
                table: "meetings");

            migrationBuilder.DropForeignKey(
                name:
                    "FK_meetings_users_student_id",
                table: "meetings");

            migrationBuilder.DropForeignKey(
                name:
                    "FK_meetings_users_supervisor_id",
                table: "meetings");

            migrationBuilder.DropForeignKey(
                name:
                    "FK_supervisor_assignments_projects_project_id",
                table: "supervisor_assignments");

            migrationBuilder.DropForeignKey(
                name:
                    "FK_supervisor_assignments_users_assigned_by_admin_id",
                table: "supervisor_assignments");

            migrationBuilder.DropForeignKey(
                name:
                    "FK_supervisor_assignments_users_student_id",
                table: "supervisor_assignments");

            migrationBuilder.DropForeignKey(
                name:
                    "FK_supervisor_assignments_users_supervisor_id",
                table: "supervisor_assignments");

            migrationBuilder.DropForeignKey(
                name:
                    "FK_supervisor_evaluations_projects_project_id",
                table: "supervisor_evaluations");

            migrationBuilder.DropIndex(
                name:
                    "IX_supervisor_evaluations_project_id_idea_id",
                table: "supervisor_evaluations");

            migrationBuilder.DropIndex(
                name:
                    "IX_supervisor_evaluations_supervisor_id_project_id",
                table: "supervisor_evaluations");

            migrationBuilder.DropIndex(
                name:
                    "IX_supervisor_assignments_project_id",
                table: "supervisor_assignments");

            migrationBuilder.DropIndex(
                name:
                    "IX_supervisor_assignments_supervisor_id_status",
                table: "supervisor_assignments");

            migrationBuilder.DropIndex(
                name:
                    "IX_meetings_project_id_scheduled_at",
                table: "meetings");

            migrationBuilder.DropIndex(
                name:
                    "IX_meetings_supervisor_id_scheduled_at",
                table: "meetings");

            migrationBuilder.DropColumn(
                name: "project_id",
                table: "supervisor_evaluations");

            migrationBuilder.DropColumn(
                name: "project_id",
                table: "supervisor_assignments");

            migrationBuilder.DropColumn(
                name:
                    "supervisor_assignment_status",
                table: "projects");

            migrationBuilder.DropColumn(
                name: "project_id",
                table: "meetings");

            migrationBuilder.AlterColumn<string>(
                name: "status",
                table: "supervisor_evaluations",
                type: "text",
                nullable: false,
                oldClrType: typeof(string),
                oldType:
                    "character varying(40)",
                oldMaxLength: 40,
                oldDefaultValue: "pending");

            migrationBuilder.AlterColumn<string>(
                name: "status",
                table: "supervisor_assignments",
                type: "text",
                nullable: false,
                oldClrType: typeof(string),
                oldType:
                    "character varying(40)",
                oldMaxLength: 40,
                oldDefaultValue:
                    "pending_admin");

            migrationBuilder.CreateIndex(
                name:
                    "IX_supervisor_evaluations_supervisor_id",
                table: "supervisor_evaluations",
                column: "supervisor_id");

            migrationBuilder.CreateIndex(
                name:
                    "IX_supervisor_assignments_supervisor_id",
                table: "supervisor_assignments",
                column: "supervisor_id");

            migrationBuilder.CreateIndex(
                name:
                    "IX_meetings_supervisor_id",
                table: "meetings",
                column: "supervisor_id");

            migrationBuilder.AddForeignKey(
                name:
                    "FK_meetings_users_student_id",
                table: "meetings",
                column: "student_id",
                principalTable: "users",
                principalColumn: "id");

            migrationBuilder.AddForeignKey(
                name:
                    "FK_meetings_users_supervisor_id",
                table: "meetings",
                column: "supervisor_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.Cascade);

            migrationBuilder.AddForeignKey(
                name:
                    "FK_supervisor_assignments_users_assigned_by_admin_id",
                table: "supervisor_assignments",
                column: "assigned_by_admin_id",
                principalTable: "users",
                principalColumn: "id");

            migrationBuilder.AddForeignKey(
                name:
                    "FK_supervisor_assignments_users_student_id",
                table: "supervisor_assignments",
                column: "student_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.Cascade);

            migrationBuilder.AddForeignKey(
                name:
                    "FK_supervisor_assignments_users_supervisor_id",
                table: "supervisor_assignments",
                column: "supervisor_id",
                principalTable: "users",
                principalColumn: "id",
                onDelete:
                    ReferentialAction.Cascade);
        }
    }
}