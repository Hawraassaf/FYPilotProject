namespace FYPilot.Domain.Entities;

/// <summary>
/// Status values for <see cref="SupervisorRegistrationRequest"/>.
///
/// Stored as a plain string column, matching this project's existing
/// status-field convention (see SupervisorAssignment.Status) rather
/// than introducing a C# enum.
/// </summary>
public static class SupervisorRegistrationStatus
{
    /// <summary>
    /// The application record exists but the email has not been
    /// verified yet.
    /// </summary>
    public const string PendingEmail = "pending_email";

    /// <summary>
    /// Email successfully verified; the applicant must still fill in
    /// the compact Supervisor academic form.
    /// </summary>
    public const string AwaitingDetails = "awaiting_details";

    /// <summary>
    /// Academic details submitted; an Admin may review the request.
    /// </summary>
    public const string PendingAdmin = "pending_admin";

    /// <summary>
    /// An Admin approved the request. The real User and
    /// SupervisorProfile now exist.
    /// </summary>
    public const string Approved = "approved";

    /// <summary>
    /// An Admin rejected the request. No User or SupervisorProfile
    /// was ever created.
    /// </summary>
    public const string Rejected = "rejected";

    /// <summary>
    /// Statuses that represent an application still in progress.
    /// Used to block a second simultaneous active application for the
    /// same email address.
    /// </summary>
    public static readonly IReadOnlyCollection<string> ActiveStatuses =
        new[] { PendingEmail, AwaitingDetails, PendingAdmin };
}
