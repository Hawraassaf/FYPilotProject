using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using FYPilot.Web.Pages.Account;
using FYPilot.Web.Pages.Admin;
using FYPilot.Web.Services.EmailVerification;
using FYPilot.Web.Services.Notifications;
using FYPilot.Web.Services.SupervisorRegistration;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Moq;

namespace FYPilot.Tests.SupervisorRegistration;

/// <summary>
/// End-to-end coverage of the Supervisor Registration Approval
/// feature: Register -> verify -> academic form -> pending_admin ->
/// Admin approve/reject, plus a Student regression check confirming
/// the existing Student flow (User + StudentProfile +
/// EmailVerificationCode) is completely untouched.
/// </summary>
public sealed class SupervisorRegistrationLifecycleTests
{
    [Fact]
    public async Task StudentRegistration_StillCreatesUserAndStudentProfile_UsingExistingEmailVerification()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);
        var tokenService = CreateTokenService();

        var emailVerificationSenderMock = new Mock<IEmailSender>();
        emailVerificationSenderMock
            .Setup(sender => sender.SendAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<string>()))
            .Returns(Task.CompletedTask);

        var emailVerificationService = new EmailVerificationService(
            db,
            emailVerificationSenderMock.Object,
            Mock.Of<ILogger<EmailVerificationService>>());

        var supervisorVerificationService = new SupervisorApplicationVerificationService(
            db,
            Mock.Of<IEmailSender>(),
            Mock.Of<ILogger<SupervisorApplicationVerificationService>>());

        var model = new RegisterModel(
            db,
            emailVerificationService,
            supervisorVerificationService,
            tokenService,
            Mock.Of<ILogger<RegisterModel>>())
        {
            PageContext = RazorPageTestHelpers.CreateAnonymousPageContext(),
            Input = new RegisterModel.InputModel
            {
                FullName = "Sam Student",
                Email = "sam.student@test.local",
                Password = "correct-horse-battery",
                Role = "student"
            }
        };

        var result = await model.OnPostAsync(CancellationToken.None);

        var redirect = Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal("/Account/VerifyEmail", redirect.PageName);

        var user = await db.Users.SingleAsync();
        Assert.Equal("student", user.Role);
        Assert.False(user.IsEmailVerified);
        Assert.True(BCrypt.Net.BCrypt.Verify("correct-horse-battery", user.PasswordHash));

        Assert.Equal(1, await db.StudentProfiles.CountAsync());
        Assert.Equal(0, await db.SupervisorProfiles.CountAsync());
        Assert.Equal(0, await db.SupervisorRegistrationRequests.CountAsync());

        var verificationCode = await db.EmailVerificationCodes.SingleAsync();
        Assert.Equal(user.Id, verificationCode.UserId);
    }

    [Fact]
    public async Task SupervisorRegistration_CreatesRequestOnly_NeverAUserOrProfile()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);
        var model = CreateRegisterModel(db, out _, out _);

        model.Input = new RegisterModel.InputModel
        {
            FullName = "Dr. Rana Supervisor",
            Email = "rana.supervisor@test.local",
            Password = "another-strong-password",
            Role = "supervisor"
        };

        var result = await model.OnPostAsync(CancellationToken.None);

        var redirect = Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal("/Account/VerifySupervisorApplication", redirect.PageName);
        Assert.NotNull(redirect.RouteValues);
        Assert.True(redirect.RouteValues!.ContainsKey("token"));
        Assert.False(string.IsNullOrWhiteSpace(redirect.RouteValues["token"] as string));

        // The most important invariant of this feature.
        Assert.Equal(0, await db.Users.CountAsync());
        Assert.Equal(0, await db.SupervisorProfiles.CountAsync());

        var request = await db.SupervisorRegistrationRequests.SingleAsync();
        Assert.Equal(SupervisorRegistrationStatus.PendingEmail, request.Status);
        Assert.Equal("rana.supervisor@test.local", request.Email);
        Assert.NotNull(request.PasswordHash);
        Assert.NotEqual("another-strong-password", request.PasswordHash);
        Assert.True(BCrypt.Net.BCrypt.Verify("another-strong-password", request.PasswordHash!));
    }

    [Fact]
    public async Task SupervisorRegistration_EmailAlreadyBelongsToAUser_IsRejected()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);

        db.Users.Add(new User
        {
            FullName = "Existing Person",
            Email = "taken@test.local",
            PasswordHash = BCrypt.Net.BCrypt.HashPassword("whatever"),
            Role = "student",
            IsEmailVerified = true
        });
        await db.SaveChangesAsync();

        var model = CreateRegisterModel(db, out _, out _);

        model.Input = new RegisterModel.InputModel
        {
            FullName = "Dr. Someone",
            Email = "taken@test.local",
            Password = "another-strong-password",
            Role = "supervisor"
        };

        var result = await model.OnPostAsync(CancellationToken.None);

        Assert.IsType<PageResult>(result);
        Assert.False(string.IsNullOrWhiteSpace(model.ErrorMessage));
        Assert.Equal(0, await db.SupervisorRegistrationRequests.CountAsync());
    }

    [Fact]
    public async Task SupervisorRegistration_ExistingActiveApplication_DoesNotCreateADuplicate()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);

        db.SupervisorRegistrationRequests.Add(new SupervisorRegistrationRequest
        {
            FullName = "Dr. Repeat Applicant",
            Email = "repeat.applicant@test.local",
            PasswordHash = BCrypt.Net.BCrypt.HashPassword("first-password"),
            Status = SupervisorRegistrationStatus.PendingEmail,
            CreatedAtUtc = DateTime.UtcNow
        });
        await db.SaveChangesAsync();

        var model = CreateRegisterModel(db, out _, out _);

        model.Input = new RegisterModel.InputModel
        {
            FullName = "Dr. Repeat Applicant",
            Email = "repeat.applicant@test.local",
            Password = "second-password-attempt",
            Role = "supervisor"
        };

        var result = await model.OnPostAsync(CancellationToken.None);

        Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal(1, await db.SupervisorRegistrationRequests.CountAsync());
    }

    [Fact]
    public async Task SupervisorApplicationDetails_ValidSubmission_MovesToPendingAdminAndNotifiesAdmins()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);

        var admin = new User
        {
            FullName = "Admin One",
            Email = "admin@test.local",
            PasswordHash = "irrelevant",
            Role = "admin",
            IsEmailVerified = true
        };
        db.Users.Add(admin);

        var request = new SupervisorRegistrationRequest
        {
            FullName = "Dr. Verified Applicant",
            Email = "verified.applicant@test.local",
            PasswordHash = BCrypt.Net.BCrypt.HashPassword("a-password"),
            Status = SupervisorRegistrationStatus.AwaitingDetails,
            CreatedAtUtc = DateTime.UtcNow,
            VerifiedAtUtc = DateTime.UtcNow
        };
        db.SupervisorRegistrationRequests.Add(request);
        await db.SaveChangesAsync();

        var tokenService = CreateTokenService();
        var token = tokenService.CreateToken(
            request.Id,
            SupervisorApplicationTokenPurpose.SupervisorDetails);

        var notificationServiceMock = new Mock<INotificationService>();
        notificationServiceMock
            .Setup(service => service.NotifyUserAsync(
                It.IsAny<int>(), It.IsAny<string>(), It.IsAny<string>(), It.IsAny<string>(),
                It.IsAny<string>(), It.IsAny<bool>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<int?>(), It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Returns(Task.CompletedTask);

        var model = new SupervisorApplicationDetailsModel(
            db,
            tokenService,
            notificationServiceMock.Object,
            Mock.Of<ILogger<SupervisorApplicationDetailsModel>>())
        {
            PageContext = RazorPageTestHelpers.CreateAnonymousPageContext(),
            Token = token,
            Input = new SupervisorApplicationDetailsModel.DetailsInputModel
            {
                AcademicTitle = "Associate Professor",
                University = "FYPilot University",
                Department = "Faculty of Computer Science",
                Specialization = "Artificial Intelligence and Data Science",
                ProfessionalProfileUrl = "https://example.edu/rana"
            }
        };

        var result = await model.OnPostAsync(CancellationToken.None);

        var redirect = Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal("/Account/SupervisorApplicationPending", redirect.PageName);

        var reloaded = await db.SupervisorRegistrationRequests.SingleAsync();
        Assert.Equal(SupervisorRegistrationStatus.PendingAdmin, reloaded.Status);
        Assert.NotNull(reloaded.SubmittedAtUtc);
        Assert.Equal("Associate Professor", reloaded.AcademicTitle);
        Assert.Equal("FYPilot University", reloaded.University);
        Assert.Equal("Faculty of Computer Science", reloaded.Department);
        Assert.Equal(
            "Artificial Intelligence and Data Science",
            reloaded.Specialization);
        Assert.Equal("https://example.edu/rana", reloaded.ProfessionalProfileUrl);

        // Still no account exists after submission.
        Assert.Equal(0, await db.Users.CountAsync(u => u.Role == "supervisor"));

        notificationServiceMock.Verify(
            service => service.NotifyUserAsync(
                admin.Id,
                "New Supervisor Registration",
                It.IsAny<string>(),
                "supervisor_registration_request",
                "/Admin/SupervisorAssignments",
                false,
                null,
                null,
                null,
                null,
                It.IsAny<CancellationToken>()),
            Times.Once);
    }

    [Fact]
    public async Task SupervisorApplicationDetails_TamperedToken_CannotReachAnotherApplicantsRequest()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);

        var requestA = new SupervisorRegistrationRequest
        {
            FullName = "Applicant A",
            Email = "applicant.a@test.local",
            Status = SupervisorRegistrationStatus.AwaitingDetails,
            CreatedAtUtc = DateTime.UtcNow,
            VerifiedAtUtc = DateTime.UtcNow
        };

        var requestB = new SupervisorRegistrationRequest
        {
            FullName = "Applicant B",
            Email = "applicant.b@test.local",
            Status = SupervisorRegistrationStatus.AwaitingDetails,
            CreatedAtUtc = DateTime.UtcNow,
            VerifiedAtUtc = DateTime.UtcNow
        };

        db.SupervisorRegistrationRequests.AddRange(requestA, requestB);
        await db.SaveChangesAsync();

        var tokenService = CreateTokenService();
        var tokenForA = tokenService.CreateToken(
            requestA.Id,
            SupervisorApplicationTokenPurpose.SupervisorDetails);

        // Simulate an attacker tampering with the token to try to
        // move between applications, rather than legitimately
        // requesting a token for B.
        var tamperedToken = RazorPageTestHelpers.TamperToken(tokenForA);

        var model = new SupervisorApplicationDetailsModel(
            db,
            tokenService,
            Mock.Of<INotificationService>(),
            Mock.Of<ILogger<SupervisorApplicationDetailsModel>>())
        {
            PageContext = RazorPageTestHelpers.CreateAnonymousPageContext(),
            Token = tamperedToken
        };

        var result = await model.OnGetAsync(CancellationToken.None);

        Assert.IsType<RedirectToPageResult>(result);

        // Neither application was modified.
        Assert.Equal(
            SupervisorRegistrationStatus.AwaitingDetails,
            (await db.SupervisorRegistrationRequests.SingleAsync(r => r.Id == requestA.Id)).Status);
        Assert.Equal(
            SupervisorRegistrationStatus.AwaitingDetails,
            (await db.SupervisorRegistrationRequests.SingleAsync(r => r.Id == requestB.Id)).Status);
    }

    [Fact]
    public async Task SupervisorApplicationDetails_RequestNotYetVerified_IsRejectedEvenWithASignedToken()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);

        var request = new SupervisorRegistrationRequest
        {
            FullName = "Not Yet Verified",
            Email = "not.verified@test.local",
            Status = SupervisorRegistrationStatus.PendingEmail,
            CreatedAtUtc = DateTime.UtcNow
        };
        db.SupervisorRegistrationRequests.Add(request);
        await db.SaveChangesAsync();

        var tokenService = CreateTokenService();

        // A details-purpose token should never legitimately exist for a
        // request that has not verified its email yet, but the page
        // must still reject it defensively if one is presented.
        var token = tokenService.CreateToken(
            request.Id,
            SupervisorApplicationTokenPurpose.SupervisorDetails);

        var model = new SupervisorApplicationDetailsModel(
            db,
            tokenService,
            Mock.Of<INotificationService>(),
            Mock.Of<ILogger<SupervisorApplicationDetailsModel>>())
        {
            PageContext = RazorPageTestHelpers.CreateAnonymousPageContext(),
            Token = token
        };

        var result = await model.OnGetAsync(CancellationToken.None);

        Assert.IsType<RedirectToPageResult>(result);
    }

    [Fact]
    public async Task AdminApproval_ValidPendingRequest_CreatesExactlyOneUserAndProfile_AndClearsSensitiveData()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);

        var admin = new User
        {
            FullName = "Admin",
            Email = "admin@test.local",
            PasswordHash = "irrelevant",
            Role = "admin",
            IsEmailVerified = true
        };
        db.Users.Add(admin);

        var request = CreateReadyForApprovalRequest("approved.applicant@test.local");
        db.SupervisorRegistrationRequests.Add(request);
        await db.SaveChangesAsync();

        var notificationServiceMock = new Mock<INotificationService>();
        notificationServiceMock
            .Setup(service => service.NotifyUserAsync(
                It.IsAny<int>(), It.IsAny<string>(), It.IsAny<string>(), It.IsAny<string>(),
                It.IsAny<string>(), It.IsAny<bool>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<int?>(), It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Returns(Task.CompletedTask);

        var emailSenderMock = new Mock<IEmailSender>();
        emailSenderMock
            .Setup(sender => sender.SendAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<string>()))
            .Returns(Task.CompletedTask);

        var model = new SupervisorAssignmentsModel(
            db,
            notificationServiceMock.Object,
            emailSenderMock.Object,
            Mock.Of<ILogger<SupervisorAssignmentsModel>>())
        {
            PageContext = RazorPageTestHelpers.CreateAuthenticatedPageContext(admin.Id, "admin")
        };

        var result = await model.OnPostApproveRegistrationAsync(
            request.Id,
            CancellationToken.None);

        Assert.IsType<RedirectToPageResult>(result);
        Assert.True(string.IsNullOrWhiteSpace(model.ErrorMessage));

        var user = await db.Users.SingleAsync(u => u.Role == "supervisor");
        Assert.Equal("approved.applicant@test.local", user.Email);
        Assert.True(user.IsEmailVerified);
        Assert.NotNull(user.EmailVerifiedAtUtc);
        Assert.False(user.MustChangePassword);
        Assert.False(user.IsMainAdmin);

        var profile = await db.SupervisorProfiles.SingleAsync();
        Assert.Equal(user.Id, profile.UserId);
        Assert.Equal("Professor", profile.AcademicTitle);
        Assert.Equal("FYPilot University", profile.University);
        Assert.Equal("Faculty of Computer Science", profile.Department);
        Assert.Equal("Artificial Intelligence and Data Science", profile.Specialization);
        Assert.Equal("https://example.edu/profile", profile.WebsiteUrl);

        var reloadedRequest = await db.SupervisorRegistrationRequests.SingleAsync();
        Assert.Equal(SupervisorRegistrationStatus.Approved, reloadedRequest.Status);
        Assert.NotNull(reloadedRequest.ReviewedAtUtc);
        Assert.Equal(admin.Id, reloadedRequest.ReviewedByAdminId);

        // Mandatory cleanup after approval.
        Assert.Null(reloadedRequest.PasswordHash);
        Assert.Null(reloadedRequest.VerificationCodeHash);

        // The original password still works for a normal login.
        Assert.True(BCrypt.Net.BCrypt.Verify("original-password", user.PasswordHash));
    }

    [Fact]
    public async Task AdminApproval_CalledTwice_DoesNotCreateADuplicateAccount()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);

        var admin = new User
        {
            FullName = "Admin",
            Email = "admin@test.local",
            PasswordHash = "irrelevant",
            Role = "admin",
            IsEmailVerified = true
        };
        db.Users.Add(admin);

        var request = CreateReadyForApprovalRequest("double.approve@test.local");
        db.SupervisorRegistrationRequests.Add(request);
        await db.SaveChangesAsync();

        var model = new SupervisorAssignmentsModel(
            db,
            Mock.Of<INotificationService>(),
            Mock.Of<IEmailSender>(),
            Mock.Of<ILogger<SupervisorAssignmentsModel>>())
        {
            PageContext = RazorPageTestHelpers.CreateAuthenticatedPageContext(admin.Id, "admin")
        };

        await model.OnPostApproveRegistrationAsync(request.Id, CancellationToken.None);
        db.ChangeTracker.Clear();

        var secondResult = await model.OnPostApproveRegistrationAsync(
            request.Id,
            CancellationToken.None);

        Assert.IsType<RedirectToPageResult>(secondResult);
        Assert.False(string.IsNullOrWhiteSpace(model.ErrorMessage));

        Assert.Equal(1, await db.Users.CountAsync(u => u.Role == "supervisor"));
        Assert.Equal(1, await db.SupervisorProfiles.CountAsync());
    }

    [Fact]
    public async Task AdminApproval_EmailAlreadyTakenByAnotherUser_RollsBackWithoutCreatingASecondUser()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);

        var admin = new User
        {
            FullName = "Admin",
            Email = "admin@test.local",
            PasswordHash = "irrelevant",
            Role = "admin",
            IsEmailVerified = true
        };
        db.Users.Add(admin);

        var request = CreateReadyForApprovalRequest("race.condition@test.local");
        db.SupervisorRegistrationRequests.Add(request);
        await db.SaveChangesAsync();

        // Simulate the same email having registered through another
        // path in between application submission and Admin review.
        db.Users.Add(new User
        {
            FullName = "Already Registered",
            Email = "race.condition@test.local",
            PasswordHash = "some-other-hash",
            Role = "student",
            IsEmailVerified = true
        });
        await db.SaveChangesAsync();

        var model = new SupervisorAssignmentsModel(
            db,
            Mock.Of<INotificationService>(),
            Mock.Of<IEmailSender>(),
            Mock.Of<ILogger<SupervisorAssignmentsModel>>())
        {
            PageContext = RazorPageTestHelpers.CreateAuthenticatedPageContext(admin.Id, "admin")
        };

        var result = await model.OnPostApproveRegistrationAsync(
            request.Id,
            CancellationToken.None);

        Assert.IsType<RedirectToPageResult>(result);
        Assert.False(string.IsNullOrWhiteSpace(model.ErrorMessage));

        Assert.Equal(1, await db.Users.CountAsync(u => u.Email == "race.condition@test.local"));
        Assert.Equal(0, await db.SupervisorProfiles.CountAsync());

        var reloadedRequest = await db.SupervisorRegistrationRequests
            .SingleAsync(r => r.Id == request.Id);
        Assert.Equal(SupervisorRegistrationStatus.PendingAdmin, reloadedRequest.Status);
    }

    [Fact]
    public async Task AdminRejection_ValidPendingRequest_CreatesNoUserOrProfile_AndClearsSensitiveData()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);

        var admin = new User
        {
            FullName = "Admin",
            Email = "admin@test.local",
            PasswordHash = "irrelevant",
            Role = "admin",
            IsEmailVerified = true
        };
        db.Users.Add(admin);

        var request = CreateReadyForApprovalRequest("rejected.applicant@test.local");
        db.SupervisorRegistrationRequests.Add(request);
        await db.SaveChangesAsync();

        var emailSenderMock = new Mock<IEmailSender>();
        emailSenderMock
            .Setup(sender => sender.SendAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<string>()))
            .Returns(Task.CompletedTask);

        var model = new SupervisorAssignmentsModel(
            db,
            Mock.Of<INotificationService>(),
            emailSenderMock.Object,
            Mock.Of<ILogger<SupervisorAssignmentsModel>>())
        {
            PageContext = RazorPageTestHelpers.CreateAuthenticatedPageContext(admin.Id, "admin")
        };

        var result = await model.OnPostRejectRegistrationAsync(
            request.Id,
            "Not a fit for the current program.",
            CancellationToken.None);

        Assert.IsType<RedirectToPageResult>(result);

        Assert.Equal(0, await db.Users.CountAsync(u => u.Role == "supervisor"));
        Assert.Equal(0, await db.SupervisorProfiles.CountAsync());

        var reloadedRequest = await db.SupervisorRegistrationRequests.SingleAsync();
        Assert.Equal(SupervisorRegistrationStatus.Rejected, reloadedRequest.Status);
        Assert.Equal(admin.Id, reloadedRequest.ReviewedByAdminId);
        Assert.NotNull(reloadedRequest.ReviewedAtUtc);
        Assert.Equal("Not a fit for the current program.", reloadedRequest.RejectionReason);

        Assert.Null(reloadedRequest.PasswordHash);
        Assert.Null(reloadedRequest.VerificationCodeHash);

        emailSenderMock.Verify(
            sender => sender.SendAsync(
                "rejected.applicant@test.local",
                It.IsAny<string>(),
                It.IsAny<string>()),
            Times.Once);
    }

    [Fact]
    public async Task PendingAndApprovedApplications_NeverAppearAsSupervisorUsers()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var db = await CreateDbAsync(connection);

        db.SupervisorRegistrationRequests.AddRange(
            new SupervisorRegistrationRequest
            {
                FullName = "Pending One",
                Email = "pending.one@test.local",
                Status = SupervisorRegistrationStatus.PendingEmail,
                CreatedAtUtc = DateTime.UtcNow
            },
            new SupervisorRegistrationRequest
            {
                FullName = "Pending Two",
                Email = "pending.two@test.local",
                Status = SupervisorRegistrationStatus.PendingAdmin,
                CreatedAtUtc = DateTime.UtcNow,
                VerifiedAtUtc = DateTime.UtcNow,
                SubmittedAtUtc = DateTime.UtcNow
            });

        await db.SaveChangesAsync();

        // Admin/Student/Supervisor analytics, matching, and dashboard
        // queries all filter on Users.Role == "supervisor" (confirmed
        // during inspection) -- with zero User rows, they are naturally
        // invisible without any extra filtering logic.
        Assert.Equal(0, await db.Users.CountAsync(u => u.Role == "supervisor"));
        Assert.Equal(2, await db.SupervisorRegistrationRequests.CountAsync());
    }

    private static async Task<ApplicationDbContext> CreateDbAsync(SqliteConnection connection)
    {
        var options = new DbContextOptionsBuilder<ApplicationDbContext>()
            .UseSqlite(connection)
            .Options;

        var db = new ApplicationDbContext(options);
        await db.Database.EnsureCreatedAsync();
        return db;
    }

    private static SupervisorApplicationTokenService CreateTokenService()
    {
        var services = new ServiceCollection();
        services.AddDataProtection();

        var provider = services.BuildServiceProvider()
            .GetRequiredService<IDataProtectionProvider>();

        return new SupervisorApplicationTokenService(provider);
    }

    private static RegisterModel CreateRegisterModel(
        ApplicationDbContext db,
        out Mock<IEmailSender> emailSenderMock,
        out SupervisorApplicationTokenService tokenService)
    {
        emailSenderMock = new Mock<IEmailSender>();
        emailSenderMock
            .Setup(sender => sender.SendAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<string>()))
            .Returns(Task.CompletedTask);

        var emailVerificationService = new EmailVerificationService(
            db,
            emailSenderMock.Object,
            Mock.Of<ILogger<EmailVerificationService>>());

        var supervisorVerificationService = new SupervisorApplicationVerificationService(
            db,
            emailSenderMock.Object,
            Mock.Of<ILogger<SupervisorApplicationVerificationService>>());

        tokenService = CreateTokenService();

        return new RegisterModel(
            db,
            emailVerificationService,
            supervisorVerificationService,
            tokenService,
            Mock.Of<ILogger<RegisterModel>>())
        {
            PageContext = RazorPageTestHelpers.CreateAnonymousPageContext()
        };
    }

    private static SupervisorRegistrationRequest CreateReadyForApprovalRequest(string email)
    {
        return new SupervisorRegistrationRequest
        {
            FullName = "Dr. Ready Applicant",
            Email = email,
            PasswordHash = BCrypt.Net.BCrypt.HashPassword("original-password"),
            AcademicTitle = "Professor",
            University = "FYPilot University",
            Department = "Faculty of Computer Science",
            Specialization = "Artificial Intelligence and Data Science",
            ProfessionalProfileUrl = "https://example.edu/profile",
            Status = SupervisorRegistrationStatus.PendingAdmin,
            CreatedAtUtc = DateTime.UtcNow.AddMinutes(-30),
            VerifiedAtUtc = DateTime.UtcNow.AddMinutes(-20),
            SubmittedAtUtc = DateTime.UtcNow.AddMinutes(-10)
        };
    }
}
