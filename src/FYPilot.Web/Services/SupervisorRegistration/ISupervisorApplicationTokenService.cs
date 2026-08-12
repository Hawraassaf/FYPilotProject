namespace FYPilot.Web.Services.SupervisorRegistration;

/// <summary>
/// Protects the SupervisorRegistrationRequest.Id that flows through
/// the browser while an applicant has no User and therefore no
/// authentication cookie.
///
/// The existing Student email-verification page identifies its
/// request purely with a raw, unsigned "userId" query/hidden-field
/// integer. That is acceptable there because the row it points at is
/// already owned by a real User. A Supervisor applicant has no such
/// account, so this token is deliberately opaque, signed, and
/// time-limited (ASP.NET Core Data Protection) instead of a raw id --
/// changing the value cannot make it resolve to a different request.
/// </summary>
public interface ISupervisorApplicationTokenService
{
    string CreateToken(
        int requestId,
        SupervisorApplicationTokenPurpose purpose);

    bool TryReadToken(
        string? token,
        SupervisorApplicationTokenPurpose purpose,
        out int requestId);
}

/// <summary>
/// A token minted for one purpose cannot be replayed for another --
/// e.g. a still-valid "verify email" link cannot be used to reach the
/// academic details form for a request that has not verified yet.
/// </summary>
public enum SupervisorApplicationTokenPurpose
{
    VerifyEmail,
    SupervisorDetails
}
