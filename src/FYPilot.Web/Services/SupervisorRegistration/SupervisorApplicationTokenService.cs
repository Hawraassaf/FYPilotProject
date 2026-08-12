using System.Globalization;
using System.Security.Cryptography;
using Microsoft.AspNetCore.DataProtection;

namespace FYPilot.Web.Services.SupervisorRegistration;

public sealed class SupervisorApplicationTokenService(
    IDataProtectionProvider dataProtectionProvider)
    : ISupervisorApplicationTokenService
{
    private static readonly TimeSpan VerifyEmailLifetime =
        TimeSpan.FromMinutes(30);

    private static readonly TimeSpan SupervisorDetailsLifetime =
        TimeSpan.FromMinutes(60);

    public string CreateToken(
        int requestId,
        SupervisorApplicationTokenPurpose purpose)
    {
        var protector = GetProtector(purpose);

        return protector.Protect(
            requestId.ToString(CultureInfo.InvariantCulture),
            GetLifetime(purpose));
    }

    public bool TryReadToken(
        string? token,
        SupervisorApplicationTokenPurpose purpose,
        out int requestId)
    {
        requestId = 0;

        if (string.IsNullOrWhiteSpace(token))
        {
            return false;
        }

        try
        {
            var protector = GetProtector(purpose);

            var payload = protector.Unprotect(token);

            return int.TryParse(
                payload,
                NumberStyles.Integer,
                CultureInfo.InvariantCulture,
                out requestId) &&
                requestId > 0;
        }
        catch (CryptographicException)
        {
            // Tampered, corrupted, or expired token.
            return false;
        }
        catch (FormatException)
        {
            return false;
        }
    }

    private ITimeLimitedDataProtector GetProtector(
        SupervisorApplicationTokenPurpose purpose)
    {
        var purposeName = purpose switch
        {
            SupervisorApplicationTokenPurpose.VerifyEmail =>
                "FYPilot.SupervisorRegistration.VerifyEmail.v1",

            SupervisorApplicationTokenPurpose.SupervisorDetails =>
                "FYPilot.SupervisorRegistration.SupervisorDetails.v1",

            _ => throw new ArgumentOutOfRangeException(
                nameof(purpose))
        };

        return dataProtectionProvider
            .CreateProtector(purposeName)
            .ToTimeLimitedDataProtector();
    }

    private static TimeSpan GetLifetime(
        SupervisorApplicationTokenPurpose purpose)
    {
        return purpose switch
        {
            SupervisorApplicationTokenPurpose.VerifyEmail =>
                VerifyEmailLifetime,

            SupervisorApplicationTokenPurpose.SupervisorDetails =>
                SupervisorDetailsLifetime,

            _ => throw new ArgumentOutOfRangeException(
                nameof(purpose))
        };
    }
}
