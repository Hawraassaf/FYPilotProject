using System.Text.RegularExpressions;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using FYPilot.Web.Services.SupervisorRegistration;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;
using Moq;

namespace FYPilot.Tests.SupervisorRegistration;

/// <summary>
/// Covers the pre-account Supervisor application verification rules:
/// six-digit code, BCrypt hash only, 10-minute lifetime, 60-second
/// resend cooldown, and 5 failed attempts -- the same security values
/// as the existing Student EmailVerificationService, applied to a
/// SupervisorRegistrationRequest instead of a User.
/// </summary>
public sealed partial class SupervisorApplicationVerificationServiceTests
{
    [Fact]
    public async Task VerifyCodeAsync_CorrectCode_MarksAwaitingDetailsAndClearsHash()
    {
        await using var scope = await TestScope.CreateAsync();

        var sendResult = await scope.Service.SendCodeAsync(scope.RequestId);
        Assert.Equal(SendSupervisorVerificationStatus.Sent, sendResult.Status);

        var code = scope.LastSentCode!;

        var result = await scope.Service.VerifyCodeAsync(scope.RequestId, code);

        Assert.Equal(VerifySupervisorCodeStatus.Verified, result.Status);

        var request = await scope.Db.SupervisorRegistrationRequests
            .SingleAsync(item => item.Id == scope.RequestId);

        Assert.Equal(SupervisorRegistrationStatus.AwaitingDetails, request.Status);
        Assert.NotNull(request.VerifiedAtUtc);
        Assert.Null(request.VerificationCodeHash);
        Assert.Null(request.VerificationSentAtUtc);
        Assert.Null(request.VerificationExpiresAtUtc);
        Assert.Equal(0, request.VerificationFailedAttemptCount);
    }

    [Fact]
    public async Task VerifyCodeAsync_WrongCode_IsRejectedAndIncrementsFailedAttempts()
    {
        await using var scope = await TestScope.CreateAsync();

        await scope.Service.SendCodeAsync(scope.RequestId);

        var wrongCode = GetCodeDifferentFrom(scope.LastSentCode!);

        var result = await scope.Service.VerifyCodeAsync(scope.RequestId, wrongCode);

        Assert.Equal(VerifySupervisorCodeStatus.InvalidCode, result.Status);
        Assert.Equal(4, result.RemainingAttempts);

        var request = await scope.Db.SupervisorRegistrationRequests
            .SingleAsync(item => item.Id == scope.RequestId);

        Assert.Equal(1, request.VerificationFailedAttemptCount);
        Assert.Equal(SupervisorRegistrationStatus.PendingEmail, request.Status);
    }

    [Fact]
    public async Task VerifyCodeAsync_FiveWrongAttempts_LocksTheCode()
    {
        await using var scope = await TestScope.CreateAsync();

        await scope.Service.SendCodeAsync(scope.RequestId);

        var wrongCode = GetCodeDifferentFrom(scope.LastSentCode!);

        VerifySupervisorCodeResult result = null!;

        for (var attempt = 0; attempt < 5; attempt++)
        {
            result = await scope.Service.VerifyCodeAsync(scope.RequestId, wrongCode);
        }

        Assert.Equal(VerifySupervisorCodeStatus.Locked, result.Status);
        Assert.Equal(0, result.RemainingAttempts);

        // The correct code must no longer work once locked.
        var afterLock = await scope.Service.VerifyCodeAsync(
            scope.RequestId,
            scope.LastSentCode!);

        Assert.Equal(VerifySupervisorCodeStatus.Locked, afterLock.Status);
    }

    [Fact]
    public async Task VerifyCodeAsync_ExpiredCode_IsRejected()
    {
        await using var scope = await TestScope.CreateAsync();

        await scope.Service.SendCodeAsync(scope.RequestId);

        var request = await scope.Db.SupervisorRegistrationRequests
            .SingleAsync(item => item.Id == scope.RequestId);

        request.VerificationExpiresAtUtc = DateTime.UtcNow.AddMinutes(-1);
        await scope.Db.SaveChangesAsync();

        var result = await scope.Service.VerifyCodeAsync(
            scope.RequestId,
            scope.LastSentCode!);

        Assert.Equal(VerifySupervisorCodeStatus.Expired, result.Status);
    }

    [Fact]
    public async Task VerifyCodeAsync_NoCodeEverSent_ReturnsNoActiveCode()
    {
        await using var scope = await TestScope.CreateAsync();

        var result = await scope.Service.VerifyCodeAsync(scope.RequestId, "123456");

        Assert.Equal(VerifySupervisorCodeStatus.NoActiveCode, result.Status);
    }

    [Fact]
    public async Task SendCodeAsync_WithinCooldown_ReturnsCooldownActive()
    {
        await using var scope = await TestScope.CreateAsync();

        var first = await scope.Service.SendCodeAsync(scope.RequestId);
        Assert.Equal(SendSupervisorVerificationStatus.Sent, first.Status);

        var second = await scope.Service.SendCodeAsync(scope.RequestId);

        Assert.Equal(SendSupervisorVerificationStatus.CooldownActive, second.Status);
        Assert.True(second.RetryAfterSeconds > 0);
    }

    [Fact]
    public async Task SendCodeAsync_AfterCooldownElapsed_ReplacesThePreviousCode()
    {
        await using var scope = await TestScope.CreateAsync();

        await scope.Service.SendCodeAsync(scope.RequestId);
        var firstCode = scope.LastSentCode!;

        var request = await scope.Db.SupervisorRegistrationRequests
            .SingleAsync(item => item.Id == scope.RequestId);

        // Simulate the 60-second cooldown having already elapsed.
        request.VerificationSentAtUtc = DateTime.UtcNow.AddSeconds(-61);
        await scope.Db.SaveChangesAsync();

        var resend = await scope.Service.SendCodeAsync(scope.RequestId);
        Assert.Equal(SendSupervisorVerificationStatus.Sent, resend.Status);

        var secondCode = scope.LastSentCode!;
        Assert.NotEqual(firstCode, secondCode);

        // Only one code can ever be valid at a time -- the old plaintext
        // code must no longer verify against the replaced hash.
        var oldCodeResult = await scope.Service.VerifyCodeAsync(scope.RequestId, firstCode);
        Assert.Equal(VerifySupervisorCodeStatus.InvalidCode, oldCodeResult.Status);

        var newCodeResult = await scope.Service.VerifyCodeAsync(scope.RequestId, secondCode);
        Assert.Equal(VerifySupervisorCodeStatus.Verified, newCodeResult.Status);
    }

    [Fact]
    public async Task SendCodeAsync_DeliveryFails_LeavesPreviousValidCodeUsable()
    {
        await using var scope = await TestScope.CreateAsync();

        await scope.Service.SendCodeAsync(scope.RequestId);
        var workingCode = scope.LastSentCode!;

        var request = await scope.Db.SupervisorRegistrationRequests
            .SingleAsync(item => item.Id == scope.RequestId);

        request.VerificationSentAtUtc = DateTime.UtcNow.AddSeconds(-61);
        await scope.Db.SaveChangesAsync();

        scope.EmailSenderMock
            .Setup(sender => sender.SendAsync(
                It.IsAny<string>(),
                It.IsAny<string>(),
                It.IsAny<string>()))
            .ThrowsAsync(new InvalidOperationException("SMTP unavailable"));

        var resend = await scope.Service.SendCodeAsync(scope.RequestId);
        Assert.Equal(SendSupervisorVerificationStatus.DeliveryFailed, resend.Status);

        // The previously working code must still verify successfully.
        var result = await scope.Service.VerifyCodeAsync(scope.RequestId, workingCode);
        Assert.Equal(VerifySupervisorCodeStatus.Verified, result.Status);
    }

    [Fact]
    public async Task SendCodeAsync_AlreadyPastPendingEmail_ReturnsAlreadyVerified()
    {
        await using var scope = await TestScope.CreateAsync();

        var request = await scope.Db.SupervisorRegistrationRequests
            .SingleAsync(item => item.Id == scope.RequestId);

        request.Status = SupervisorRegistrationStatus.AwaitingDetails;
        await scope.Db.SaveChangesAsync();

        var result = await scope.Service.SendCodeAsync(scope.RequestId);

        Assert.Equal(SendSupervisorVerificationStatus.AlreadyVerified, result.Status);
    }

    [GeneratedRegex(">(\\d{6})<")]
    private static partial Regex CodeExtractionRegex();

    private static string GetCodeDifferentFrom(string code)
    {
        return code == "111111" ? "222222" : "111111";
    }

    private sealed class TestScope : IAsyncDisposable
    {
        private TestScope(
            SqliteConnection connection,
            ApplicationDbContext db,
            SupervisorApplicationVerificationService service,
            Mock<IEmailSender> emailSenderMock,
            int requestId)
        {
            Connection = connection;
            Db = db;
            Service = service;
            EmailSenderMock = emailSenderMock;
            RequestId = requestId;

            EmailSenderMock
                .Setup(sender => sender.SendAsync(
                    It.IsAny<string>(),
                    It.IsAny<string>(),
                    It.IsAny<string>()))
                .Callback<string, string, string>((_, _, body) =>
                {
                    var match = CodeExtractionRegex().Match(body);
                    LastSentCode = match.Success ? match.Groups[1].Value : null;
                })
                .Returns(Task.CompletedTask);
        }

        public SqliteConnection Connection { get; }
        public ApplicationDbContext Db { get; }
        public SupervisorApplicationVerificationService Service { get; }
        public Mock<IEmailSender> EmailSenderMock { get; }
        public int RequestId { get; }
        public string? LastSentCode { get; private set; }

        public static async Task<TestScope> CreateAsync()
        {
            var connection = new SqliteConnection("Data Source=:memory:");
            await connection.OpenAsync();

            var options = new DbContextOptionsBuilder<ApplicationDbContext>()
                .UseSqlite(connection)
                .Options;

            var db = new ApplicationDbContext(options);
            await db.Database.EnsureCreatedAsync();

            var request = new SupervisorRegistrationRequest
            {
                FullName = "Dr. Jane Applicant",
                Email = "jane.applicant@test.local",
                PasswordHash = "irrelevant-for-this-suite",
                Status = SupervisorRegistrationStatus.PendingEmail,
                CreatedAtUtc = DateTime.UtcNow
            };

            db.SupervisorRegistrationRequests.Add(request);
            await db.SaveChangesAsync();

            var emailSenderMock = new Mock<IEmailSender>();

            var service = new SupervisorApplicationVerificationService(
                db,
                emailSenderMock.Object,
                Mock.Of<ILogger<SupervisorApplicationVerificationService>>());

            return new TestScope(
                connection,
                db,
                service,
                emailSenderMock,
                request.Id);
        }

        public async ValueTask DisposeAsync()
        {
            await Db.DisposeAsync();
            await Connection.DisposeAsync();
        }
    }
}
