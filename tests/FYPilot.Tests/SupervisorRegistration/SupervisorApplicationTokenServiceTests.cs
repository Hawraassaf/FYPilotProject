using FYPilot.Web.Services.SupervisorRegistration;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.Extensions.DependencyInjection;

namespace FYPilot.Tests.SupervisorRegistration;

/// <summary>
/// A Supervisor applicant has no User and no authentication cookie,
/// so the request id must travel as a signed, time-limited Data
/// Protection token instead of a raw integer -- unlike the existing
/// Student VerifyEmail page, which does use a raw "userId". These
/// tests confirm the token cannot be forged, cannot be replayed for
/// a different purpose, and round-trips the correct id.
/// </summary>
public sealed class SupervisorApplicationTokenServiceTests
{
    [Fact]
    public void CreateToken_ThenTryReadToken_RoundTripsRequestId()
    {
        var service = CreateService();

        var token = service.CreateToken(
            42,
            SupervisorApplicationTokenPurpose.VerifyEmail);

        var success = service.TryReadToken(
            token,
            SupervisorApplicationTokenPurpose.VerifyEmail,
            out var requestId);

        Assert.True(success);
        Assert.Equal(42, requestId);
    }

    [Fact]
    public void TryReadToken_WrongPurpose_Fails()
    {
        var service = CreateService();

        var token = service.CreateToken(
            42,
            SupervisorApplicationTokenPurpose.VerifyEmail);

        var success = service.TryReadToken(
            token,
            SupervisorApplicationTokenPurpose.SupervisorDetails,
            out var requestId);

        Assert.False(success);
        Assert.Equal(0, requestId);
    }

    [Fact]
    public void TryReadToken_TamperedToken_Fails()
    {
        var service = CreateService();

        var token = service.CreateToken(
            42,
            SupervisorApplicationTokenPurpose.VerifyEmail);

        // An attacker changing which application id the token
        // resolves to must not succeed.
        var tampered = RazorPageTestHelpers.TamperToken(token);

        var success = service.TryReadToken(
            tampered,
            SupervisorApplicationTokenPurpose.VerifyEmail,
            out var requestId);

        Assert.False(success);
        Assert.Equal(0, requestId);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("not-a-real-token")]
    public void TryReadToken_MissingOrGarbageToken_Fails(string? token)
    {
        var service = CreateService();

        var success = service.TryReadToken(
            token,
            SupervisorApplicationTokenPurpose.VerifyEmail,
            out var requestId);

        Assert.False(success);
        Assert.Equal(0, requestId);
    }

    private static SupervisorApplicationTokenService CreateService()
    {
        var services = new ServiceCollection();
        services.AddDataProtection();

        var provider = services.BuildServiceProvider()
            .GetRequiredService<IDataProtectionProvider>();

        return new SupervisorApplicationTokenService(provider);
    }
}
