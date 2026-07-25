using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace FYPilot.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class RemoveLegacyStudentSupervisorUniqueness : Migration
    {
        /// <inheritdoc />
        protected override void Up(
    MigrationBuilder migrationBuilder)
        {
            migrationBuilder.Sql(
                """
        /*
         * Remove the old student-based uniqueness rule.
         *
         * PostgreSQL can represent it as either a table
         * constraint or a unique index, so handle both.
         */
        DO $$
        BEGIN
            IF EXISTS
            (
                SELECT 1
                FROM pg_constraint
                WHERE conname =
                    'ux_supervisor_assignments_one_active_or_pending_student'
            )
            THEN
                ALTER TABLE public.supervisor_assignments
                DROP CONSTRAINT
                    ux_supervisor_assignments_one_active_or_pending_student;
            END IF;
        END
        $$;

        DROP INDEX IF EXISTS
            public.ux_supervisor_assignments_one_active_or_pending_student;

        /*
         * Keep the correct project-based rule.
         *
         * One project can have only one pending or active
         * supervisor assignment.
         */
        CREATE UNIQUE INDEX IF NOT EXISTS
            "IX_supervisor_assignments_project_id"
        ON public.supervisor_assignments
            (project_id)
        WHERE project_id IS NOT NULL
          AND status IN
          (
              'pending_admin',
              'active'
          );
        """);
        }

        protected override void Down(
            MigrationBuilder migrationBuilder)
        {
            /*
             * The old student-based rule is intentionally not
             * restored because one student may belong to several
             * projects with different supervisors.
             */
        }
    }
}
